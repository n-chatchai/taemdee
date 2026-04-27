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
from app.models import Branch, Redemption, Shop, Point
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


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    ref: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
):
    """Shop login + first-time signup (same OTP/LINE form).

    `?ref=<code>` carries through the Shop→Shop referral grant.
    """
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


@router.post("/login")
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


# Back-compat: /shop/register still works (referral links printed before the rename
# point here). Preserves any ?ref= query string when forwarding.
@router.get("/register")
async def register_legacy_redirect(ref: Optional[str] = None):
    target = "/shop/login" + (f"?ref={ref}" if ref else "")
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    if not shop.is_onboarded:
        return RedirectResponse(url="/shop/onboard", status_code=status.HTTP_303_SEE_OTHER)

    now = utcnow()
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    # Headline: distinct customers stamped this week (proxy for "came back")
    customers_this_week = (await db.exec(
        select(func.count(func.distinct(Point.customer_id)))
        .where(Point.shop_id == shop.id, Point.created_at >= week_ago, Point.is_voided == False)  # noqa: E712
    )).one()
    customers_last_week = (await db.exec(
        select(func.count(func.distinct(Point.customer_id)))
        .where(
            Point.shop_id == shop.id,
            Point.created_at >= two_weeks_ago,
            Point.created_at < week_ago,
            Point.is_voided == False,  # noqa: E712
        )
    )).one()
    wow_delta = customers_this_week - customers_last_week
    points_total = (await db.exec(
        select(func.count()).select_from(Point)
        .where(Point.shop_id == shop.id, Point.is_voided == False)  # noqa: E712
    )).one()
    redemptions_total = (await db.exec(
        select(func.count()).select_from(Redemption)
        .where(Redemption.shop_id == shop.id, Redemption.is_voided == False)  # noqa: E712
    )).one()
    branches_count = (await db.exec(
        select(func.count()).select_from(Branch).where(Branch.shop_id == shop.id)
    )).one()
    # The branch-pill only shows for multi-branch shops (>=2 branches), per
    # PRD §6.I — single-branch shops see no branch UI anywhere. Label is the
    # first branch's name; the dashboard template gates on branches_count.
    branch_label = None
    if branches_count > 1:
        first_branch = (await db.exec(
            select(Branch).where(Branch.shop_id == shop.id).order_by(Branch.created_at)
        )).first()
        branch_label = first_branch.name if first_branch else None

    weekday_th = ("วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์", "วันเสาร์", "วันอาทิตย์")[now.weekday()]

    # Live feed: most recent 8 points + redemptions, merged. Each entry
    # carries the customer's display name so the dock can render
    # "เพิ่งเกิด ★ สมศรี" instead of "#A12B" — the row is the unit the
    # owner taps to void, so a recognisable name matters.
    recent_points = (await db.exec(
        select(Point).where(Point.shop_id == shop.id)
        .order_by(Point.created_at.desc()).limit(8)
    )).all()
    recent_redemptions = (await db.exec(
        select(Redemption).where(Redemption.shop_id == shop.id)
        .order_by(Redemption.created_at.desc()).limit(4)
    )).all()

    customer_ids = {p.customer_id for p in recent_points} | {r.customer_id for r in recent_redemptions}
    customers_by_id = {}
    if customer_ids:
        from app.models import Customer
        rows = (await db.exec(select(Customer).where(Customer.id.in_(customer_ids)))).all()
        customers_by_id = {c.id: (c.display_name or "ลูกค้า") for c in rows}

    feed = sorted(
        [("point", p, customers_by_id.get(p.customer_id, "ลูกค้า")) for p in recent_points]
        + [("redemption", r, customers_by_id.get(r.customer_id, "ลูกค้า")) for r in recent_redemptions],
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
            "wow_delta": wow_delta,
            "points_total": points_total,
            "redemptions_total": redemptions_total,
            "feed": feed,
            "suggestions": suggestions,
            "weekday_th": weekday_th,
            "branches_count": branches_count,
            "branch_label": branch_label,
        },
    )


VALID_THEMES = ("taemdee", "mono", "night", "pastel")
TOPUP_PACKAGES = {
    "small":   {"credits": 100,   "bonus": 0,   "price": 100,   "label": "ส่งดีรีชได้ ~2 ครั้ง"},
    "popular": {"credits": 220,   "bonus": 20,  "price": 200,   "label": "ส่งดีรีชได้ ~4 ครั้ง · เหมาะกับร้านใหม่"},
    "big":     {"credits": 1200,  "bonus": 200, "price": 1000,  "label": "ส่งดีรีชได้ ~24 ครั้ง"},
}


# --------- Onboarding wizard (4 steps) ---------

VALID_REWARD_IMAGES = {"gift_box", "card", "star", "coffee_cup"}
VALID_REWARD_GOALS = (5, 10, 20)


@router.get("/onboard")
async def onboard_redirect():
    """Back-compat: legacy single-page onboard now redirects into the wizard."""
    return RedirectResponse(url="/shop/onboard/identity", status_code=status.HTTP_303_SEE_OTHER)


# Legacy URLs from the previous wizard layout — redirect into the new flow.
@router.get("/onboard/name")
async def onboard_name_legacy_redirect():
    return RedirectResponse(url="/shop/onboard/identity", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/onboard/logo")
async def onboard_logo_legacy_redirect():
    return RedirectResponse(url="/shop/onboard/reward", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/onboard/identity", response_class=HTMLResponse)
async def onboard_identity_get(
    request: Request,
    gen: int = 0,
    shop: Shop = Depends(get_current_shop),
):
    """Step 1/4 — shop name + AI logo picker on the same screen."""
    options = generate_logos(shop.name, seed=gen)
    picked_id = (
        shop.logo_url[5:] if shop.logo_url and shop.logo_url.startswith("text:") else None
    )
    saved_pick = None
    if picked_id and picked_id not in {o["id"] for o in options} and picked_id in VALID_STYLE_IDS:
        saved_pick = render_style(shop.name, picked_id)
    return templates.TemplateResponse(
        request=request,
        name="shop/onboard/identity.html",
        context={
            "shop": shop,
            "options": options,
            "saved_pick": saved_pick,
            "gen": gen,
            "next_gen": gen + 1,
        },
    )


@router.post("/onboard/identity")
async def onboard_identity_post(
    name: str = Form(...),
    logo_choice: Optional[str] = Form(None),
    province: Optional[str] = Form(None),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    shop.name = name.strip() or shop.name
    if logo_choice in VALID_STYLE_IDS:
        shop.logo_url = f"text:{logo_choice}"
    # Province lands in the existing Shop.location field — district + detail
    # come later via S10.location (deferred). Empty submission keeps current.
    cleaned_province = (province or "").strip()
    if cleaned_province:
        shop.location = cleaned_province
    db.add(shop)
    await db.commit()
    return RedirectResponse(url="/shop/onboard/reward", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/onboard/reward", response_class=HTMLResponse)
async def onboard_reward_get(request: Request, shop: Shop = Depends(get_current_shop)):
    """Step 2/4 — reward description + image picker (3 CSS-drawn options) + threshold pills."""
    return templates.TemplateResponse(
        request=request,
        name="shop/onboard/reward.html",
        context={
            "shop": shop,
            "reward_images": ["gift_box", "card", "star", "coffee_cup"],
            "goals": VALID_REWARD_GOALS,
        },
    )


@router.post("/onboard/reward")
async def onboard_reward_post(
    reward_description: str = Form(...),
    reward_image: Optional[str] = Form(None),
    reward_threshold: int = Form(10),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    shop.reward_description = reward_description.strip() or shop.reward_description
    if reward_image in VALID_REWARD_IMAGES:
        shop.reward_image = reward_image
    # Allow the canonical pills (5/10/20) plus any custom value 1–99.
    if 1 <= reward_threshold <= 99:
        shop.reward_threshold = reward_threshold
    db.add(shop)
    await db.commit()
    return RedirectResponse(url="/shop/onboard/theme", status_code=status.HTTP_303_SEE_OTHER)


# Settings-context reward editor — same fields as the onboarding step, but with
# app-bar chrome (back to /shop/settings, single "บันทึก" button) instead of the
# wizard's step counter and "ถัดไป · เลือกธีม" advance.
@router.get("/reward", response_class=HTMLResponse)
async def reward_edit_get(request: Request, shop: Shop = Depends(get_current_shop)):
    return templates.TemplateResponse(
        request=request,
        name="shop/reward_edit.html",
        context={
            "shop": shop,
            "reward_images": ["gift_box", "card", "star", "coffee_cup"],
            "goals": VALID_REWARD_GOALS,
        },
    )


@router.post("/reward")
async def reward_edit_post(
    reward_description: str = Form(...),
    reward_image: Optional[str] = Form(None),
    reward_threshold: int = Form(10),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    shop.reward_description = reward_description.strip() or shop.reward_description
    if reward_image in VALID_REWARD_IMAGES:
        shop.reward_image = reward_image
    if 1 <= reward_threshold <= 99:
        shop.reward_threshold = reward_threshold
    db.add(shop)
    await db.commit()
    return RedirectResponse(url="/shop/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/onboard/theme", response_class=HTMLResponse)
async def onboard_theme_get(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    # Eagerly load branches for the live preview in the template
    await db.refresh(shop, ["branches"])
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
    """S2.4 — celebration + the shop's actual print QR ready to download.
    Per the revised design the customer-preview mini phone moved out; the
    onboarding wraps with "here's your QR, save it, then enter the app"."""
    scan_url = str(request.base_url).rstrip("/") + f"/scan/{shop.id}"
    qr_svg = segno.make(scan_url, error="m").svg_inline(
        scale=8, dark="#111111", light="#ffffff", border=1, omitsize=True
    )
    return templates.TemplateResponse(
        request=request,
        name="shop/onboard/done.html",
        context={"shop": shop, "scan_url": scan_url, "qr_svg": qr_svg},
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
async def themes_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    await db.refresh(shop, ["branches"])
    branch_label = shop.branches[0].name if shop.branches else (shop.location or "ทุกสาขา")
    return templates.TemplateResponse(
        request=request,
        name="shop/themes.html",
        context={"shop": shop, "themes": VALID_THEMES, "branch_label": branch_label},
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
    db: AsyncSession = Depends(get_session),
):
    await db.refresh(shop, ["branches"])
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
    share_url = f"{base_url}/shop/login?ref={referral.code}"
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


@router.get("/qr.png")
async def shop_qr_png(
    request: Request,
    shop: Shop = Depends(get_current_shop),
):
    """High-DPI PNG of just the QR for the บันทึก (save) button on S8.

    Browsers respect the Content-Disposition filename, so the file lands as
    `taemdee-qr-<shop>.png` in the user's Downloads folder. Plain QR only —
    the framing/reward card is for `window.print()` from the page itself.
    """
    scan_url = str(request.base_url).rstrip("/") + f"/scan/{shop.id}"
    buf = io.BytesIO()
    segno.make(scan_url, error="m").save(buf, kind="png", scale=20, border=2)
    safe_name = "".join(c if c.isalnum() else "-" for c in shop.name).strip("-").lower() or "shop"
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="taemdee-qr-{safe_name}.png"'},
    )
