import io
from datetime import timedelta
from typing import Optional

import segno
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from app.core.templates import templates
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_shop
from app.core.database import get_session
from app.models import Redemption, Shop, Stamp
from app.models.util import utcnow
from app.routes.auth import _set_session_cookie
from app.services.auth import issue_session_token
from app.services.deereach import compute_suggestions
from app.services.events import stream as event_stream
from app.services.logo_gen import VALID_STYLE_IDS, generate_logos, render_style
from app.services.referrals import (
    complete_referral_for,
    create_referral_code,
    find_referral_by_code,
)

router = APIRouter()


@router.get("/register", response_class=HTMLResponse)
async def register_page(
    request: Request,
    ref: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
):
    referrer = None
    if ref:
        referral = await find_referral_by_code(db, ref)
        if referral and referral.referee_shop_id is None:
            referrer = await db.get(Shop, referral.referrer_shop_id)
    return templates.TemplateResponse(
        request=request,
        name="shop/register.html",
        context={"ref_code": ref, "referrer": referrer},
    )


@router.post("/register")
async def dev_login_or_register(
    request: Request,
    response: Response,
    phone: str = Form(...),
    name: str = Form("New Shop"),
    db: AsyncSession = Depends(get_session),
):
    """DEV-ONLY shortcut: the template's mock OTP flow lands here.

    Creates the shop if it doesn't exist, sets a session cookie, and redirects to the
    dashboard. Real OTP verification happens at `/auth/otp/verify` — this endpoint
    exists so the current demo UI keeps working until the proper OTP form replaces it.
    """
    result = await db.exec(select(Shop).where(Shop.phone == phone))
    shop = result.first()

    if not shop:
        shop = Shop(name=name, phone=phone)
        db.add(shop)
        await db.commit()
        await db.refresh(shop)

    redirect = RedirectResponse(url="/shop/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(redirect, issue_session_token(shop.id))
    return redirect


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    if not shop.is_onboarded:
        return RedirectResponse(url="/shop/onboard", status_code=status.HTTP_303_SEE_OTHER)

    week_ago = utcnow() - timedelta(days=7)

    # Headline: distinct customers stamped this week (proxy for "came back")
    customers_this_week = (await db.exec(
        select(func.count(func.distinct(Stamp.customer_id)))
        .where(Stamp.shop_id == shop.id, Stamp.created_at >= week_ago, Stamp.is_voided == False)  # noqa: E712
    )).one()
    stamps_total = (await db.exec(
        select(func.count()).select_from(Stamp)
        .where(Stamp.shop_id == shop.id, Stamp.is_voided == False)  # noqa: E712
    )).one()
    redemptions_total = (await db.exec(
        select(func.count()).select_from(Redemption)
        .where(Redemption.shop_id == shop.id, Redemption.is_voided == False)  # noqa: E712
    )).one()

    # Live feed: most recent 8 stamps + redemptions, merged
    recent_stamps = (await db.exec(
        select(Stamp).where(Stamp.shop_id == shop.id)
        .order_by(Stamp.created_at.desc()).limit(8)
    )).all()
    recent_redemptions = (await db.exec(
        select(Redemption).where(Redemption.shop_id == shop.id)
        .order_by(Redemption.created_at.desc()).limit(4)
    )).all()
    feed = sorted(
        [("stamp", s) for s in recent_stamps] + [("redemption", r) for r in recent_redemptions],
        key=lambda x: x[1].created_at,
        reverse=True,
    )[:8]

    suggestions = await compute_suggestions(db, shop)

    return templates.TemplateResponse(
        request=request,
        name="shop/dashboard.html",
        context={
            "shop": shop,
            "customers_this_week": customers_this_week,
            "stamps_total": stamps_total,
            "redemptions_total": redemptions_total,
            "feed": feed,
            "suggestions": suggestions,
        },
    )


VALID_THEMES = ("taemdee", "mono", "night", "pastel")
TOPUP_PACKAGES = {
    "small":   {"credits": 100,   "bonus": 0,   "price": 100,   "label": "ส่งดีรีชได้ ~2 ครั้ง"},
    "popular": {"credits": 220,   "bonus": 20,  "price": 200,   "label": "ส่งดีรีชได้ ~4 ครั้ง · เหมาะกับร้านใหม่"},
    "big":     {"credits": 1200,  "bonus": 200, "price": 1000,  "label": "ส่งดีรีชได้ ~24 ครั้ง"},
}


# --------- Onboarding wizard (4 steps) ---------

@router.get("/onboard")
async def onboard_redirect():
    """Back-compat: legacy single-page onboard now redirects into the wizard."""
    return RedirectResponse(url="/shop/onboard/name", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/onboard/name", response_class=HTMLResponse)
async def onboard_name_get(request: Request, shop: Shop = Depends(get_current_shop)):
    return templates.TemplateResponse(
        request=request,
        name="shop/onboard/name.html",
        context={"shop": shop},
    )


@router.post("/onboard/name")
async def onboard_name_post(
    name: str = Form(...),
    reward_description: str = Form(...),
    reward_threshold: int = Form(10),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    if reward_threshold not in (8, 10, 15):
        reward_threshold = 10
    shop.name = name.strip() or shop.name
    shop.reward_description = reward_description.strip() or shop.reward_description
    shop.reward_threshold = reward_threshold
    db.add(shop)
    await db.commit()
    return RedirectResponse(url="/shop/onboard/logo", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/onboard/logo", response_class=HTMLResponse)
async def onboard_logo_get(
    request: Request,
    gen: int = 0,
    shop: Shop = Depends(get_current_shop),
):
    options = generate_logos(shop.name, seed=gen)
    picked_id = (
        shop.logo_url[5:] if shop.logo_url and shop.logo_url.startswith("text:") else None
    )
    # If the saved pick isn't in the current trio, render it as a 4th "your pick" so
    # the owner doesn't lose their previous choice when they hit regenerate.
    saved_pick = None
    if picked_id and picked_id not in {o["id"] for o in options} and picked_id in VALID_STYLE_IDS:
        saved_pick = render_style(shop.name, picked_id)
    return templates.TemplateResponse(
        request=request,
        name="shop/onboard/logo.html",
        context={
            "shop": shop,
            "options": options,
            "saved_pick": saved_pick,
            "gen": gen,
            "next_gen": gen + 1,
        },
    )


@router.post("/onboard/logo")
async def onboard_logo_post(
    logo_choice: Optional[str] = Form(None),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    if logo_choice in VALID_STYLE_IDS:
        shop.logo_url = f"text:{logo_choice}"
        db.add(shop)
        await db.commit()
    return RedirectResponse(url="/shop/onboard/theme", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/onboard/theme", response_class=HTMLResponse)
async def onboard_theme_get(request: Request, shop: Shop = Depends(get_current_shop)):
    return templates.TemplateResponse(
        request=request,
        name="shop/onboard/theme.html",
        context={"shop": shop, "themes": VALID_THEMES},
    )


@router.post("/onboard/theme")
async def onboard_theme_post(
    theme: str = Form("taemdee"),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    if theme in VALID_THEMES:
        shop.theme_name = theme
        db.add(shop)
        await db.commit()
    return RedirectResponse(url="/shop/onboard/done", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/onboard/done", response_class=HTMLResponse)
async def onboard_done_get(
    request: Request,
    shop: Shop = Depends(get_current_shop),
):
    scan_url = str(request.base_url).rstrip("/") + f"/scan/{shop.id}"
    qr_svg = segno.make(scan_url, error="m").svg_inline(
        scale=4, dark="#111111", light="#ffffff", border=0, omitsize=True
    )
    return templates.TemplateResponse(
        request=request,
        name="shop/onboard/done.html",
        context={"shop": shop, "qr_svg": qr_svg},
    )


@router.post("/onboard/done")
async def onboard_done_post(
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    shop.is_onboarded = True
    db.add(shop)
    await db.commit()
    await complete_referral_for(db, shop)
    return RedirectResponse(url="/shop/dashboard", status_code=status.HTTP_303_SEE_OTHER)


# --------- Top-up (S7 + S7 confirm) — UI placeholder; Slip2Go integration in R7 ---------

@router.get("/topup", response_class=HTMLResponse)
async def topup_page(request: Request, shop: Shop = Depends(get_current_shop)):
    cost_per_send = 50  # rough estimate for the "≈ ส่งดีรีชได้ N ครั้ง" line
    sends_remaining = shop.credit_balance // cost_per_send if cost_per_send else 0
    return templates.TemplateResponse(
        request=request,
        name="shop/topup.html",
        context={
            "shop": shop,
            "packages": TOPUP_PACKAGES,
            "sends_remaining": sends_remaining,
        },
    )


@router.get("/topup/confirm", response_class=HTMLResponse)
async def topup_confirm_page(
    request: Request,
    pkg: str,
    shop: Shop = Depends(get_current_shop),
):
    if pkg not in TOPUP_PACKAGES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown package")
    package = TOPUP_PACKAGES[pkg]
    # Placeholder PromptPay QR — real PromptPay payload generated in R7 with Slip2Go.
    promptpay_payload = f"taemdee-promptpay-stub-{package['price']}-thb"
    qr_svg = segno.make(promptpay_payload, error="m").svg_inline(
        scale=4, dark="#111111", light="#ffffff", border=1, omitsize=True
    )
    return templates.TemplateResponse(
        request=request,
        name="shop/topup_confirm.html",
        context={"shop": shop, "package": package, "pkg_id": pkg, "qr_svg": qr_svg},
    )


# --------- Theme picker (S9) ---------

@router.get("/themes", response_class=HTMLResponse)
async def themes_page(request: Request, shop: Shop = Depends(get_current_shop)):
    return templates.TemplateResponse(
        request=request,
        name="shop/themes.html",
        context={"shop": shop, "themes": VALID_THEMES},
    )


@router.post("/themes")
async def themes_save(
    theme: str = Form(...),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    if theme not in VALID_THEMES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown theme")
    shop.theme_name = theme
    db.add(shop)
    await db.commit()
    return RedirectResponse(url="/shop/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/events")
async def shop_events(
    shop: Shop = Depends(get_current_shop),
):
    """Server-Sent Events stream for the DeeBoard's live feed."""
    return StreamingResponse(
        event_stream(shop.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
):
    return templates.TemplateResponse(
        request=request,
        name="shop/settings.html",
        context={"shop": shop},
    )


@router.get("/refer", response_class=HTMLResponse)
async def refer_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    referral = await create_referral_code(db, shop)
    base_url = str(request.base_url).rstrip("/")
    share_url = f"{base_url}/shop/register?ref={referral.code}"
    return templates.TemplateResponse(
        request=request,
        name="shop/refer.html",
        context={"shop": shop, "share_url": share_url, "referral": referral},
    )


@router.get("/qr", response_class=HTMLResponse)
async def shop_qr(
    request: Request,
    shop: Shop = Depends(get_current_shop),
):
    scan_url = str(request.base_url).rstrip("/") + f"/scan/{shop.id}"
    qr_svg = segno.make(scan_url, error="m").svg_inline(
        scale=8, dark="#111111", light="#ffffff", border=1, omitsize=True
    )
    return templates.TemplateResponse(
        request=request,
        name="shop/qr.html",
        context={"shop": shop, "scan_url": scan_url, "qr_svg": qr_svg},
    )
