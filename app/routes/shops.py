import io
from datetime import timedelta, timezone
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

    # Today / yesterday slices for the snapshot card. Day boundaries use
    # Bangkok local time so a 23:55 stamp doesn't get filed under "yesterday"
    # when the owner glances at the dashboard at 00:05.
    from app.models.util import BKK
    bkk_now = now.replace(tzinfo=timezone.utc).astimezone(BKK)
    today_start_bkk = bkk_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_bkk.astimezone(timezone.utc).replace(tzinfo=None)
    yesterday_start_utc = today_start_utc - timedelta(days=1)

    customers_today = (await db.exec(
        select(func.count(func.distinct(Point.customer_id)))
        .where(
            Point.shop_id == shop.id,
            Point.created_at >= today_start_utc,
            Point.is_voided == False,  # noqa: E712
        )
    )).one()
    customers_yesterday = (await db.exec(
        select(func.count(func.distinct(Point.customer_id)))
        .where(
            Point.shop_id == shop.id,
            Point.created_at >= yesterday_start_utc,
            Point.created_at < today_start_utc,
            Point.is_voided == False,  # noqa: E712
        )
    )).one()
    today_delta = customers_today - customers_yesterday

    points_today = (await db.exec(
        select(func.count()).select_from(Point)
        .where(
            Point.shop_id == shop.id,
            Point.created_at >= today_start_utc,
            Point.is_voided == False,  # noqa: E712
        )
    )).one()
    redemptions_today = (await db.exec(
        select(func.count()).select_from(Redemption)
        .where(
            Redemption.shop_id == shop.id,
            Redemption.created_at >= today_start_utc,
            Redemption.is_voided == False,  # noqa: E712
        )
    )).one()

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

    # 7-day point counts for the trend bars (today is rightmost). Pre-fetch
    # all points in window then bucket by BKK day in Python — one query
    # beats issuing seven `count(...) where day = N` queries.
    week_start_utc = today_start_utc - timedelta(days=6)
    point_rows = (await db.exec(
        select(Point.created_at)
        .where(
            Point.shop_id == shop.id,
            Point.created_at >= week_start_utc,
            Point.is_voided == False,  # noqa: E712
        )
    )).all()
    daily_counts = [0] * 7
    for created_at in point_rows:
        bkk_date = created_at.replace(tzinfo=timezone.utc).astimezone(BKK).date()
        offset_days = (today_start_bkk.date() - bkk_date).days
        if 0 <= offset_days < 7:
            daily_counts[6 - offset_days] += 1
    max_daily = max(daily_counts) or 1
    daily_pct = [int(round(100 * c / max_daily)) for c in daily_counts]
    trend_total = sum(daily_counts)

    # Week-over-week percentage for the trend header
    prev_week_total = (await db.exec(
        select(func.count()).select_from(Point)
        .where(
            Point.shop_id == shop.id,
            Point.created_at >= two_weeks_ago,
            Point.created_at < week_ago,
            Point.is_voided == False,  # noqa: E712
        )
    )).one()
    if prev_week_total:
        wow_pct = int(round(100 * (trend_total - prev_week_total) / prev_week_total))
    else:
        wow_pct = None

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
    # PRD §6.I — single-branch shops see no branch UI anywhere.
    branch_label = None
    if branches_count > 1:
        first_branch = (await db.exec(
            select(Branch).where(Branch.shop_id == shop.id).order_by(Branch.created_at)
        )).first()
        branch_label = first_branch.name if first_branch else None

    weekday_th = ("วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์", "วันเสาร์", "วันอาทิตย์")[now.weekday()]

    # Build the "แต้มดีแนะนำ" attention cards from current state. Three slots:
    # warn (low credits), opp (near-ready customers), ai (suggestion count).
    suggestions = await compute_suggestions(db, shop)
    attn_cards = []
    if shop.credit_balance < 100:
        attn_cards.append({
            "kind": "warn",
            "head": f"เครดิตเหลือ {shop.credit_balance} · ใกล้หมด",
            "sub": "เติมก่อนใช้ส่งโปรชวนกลับ",
            "link": "/shop/topup",
        })
    near_ready = next((s for s in suggestions if s.kind == "almost_there"), None)
    if near_ready:
        attn_cards.append({
            "kind": "opp",
            "head": f"{near_ready.audience_count} ลูกค้าใกล้รับรางวัล",
            "sub": near_ready.body,
            "link": "/shop/customers?filter=near",
        })
    if suggestions:
        attn_cards.append({
            "kind": "ai",
            "head": suggestions[0].head,
            "sub": f"{len(suggestions)} แคมเปญที่ระบบแนะนำ · เพิ่มได้",
            "link": "/shop/insights",
        })

    from app.core.config import settings as app_settings

    return templates.TemplateResponse(
        request=request,
        name="shop/dashboard.html",
        context={
            "shop": shop,
            "customers_today": customers_today,
            "today_delta": today_delta,
            "points_today": points_today,
            "redemptions_today": redemptions_today,
            "customers_this_week": customers_this_week,
            "wow_delta": wow_delta,
            "wow_pct": wow_pct,
            "daily_pct": daily_pct,
            "daily_counts": daily_counts,
            "points_total": points_total,
            "redemptions_total": redemptions_total,
            "feed_cap": app_settings.shop_customer_last_scan_display_number,
            "attn_cards": attn_cards,
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


# ── S3.insights ─────────────────────────────────────────────────────────────
# Tabbed "แต้มดีแนะนำ" hub. The toggle on top swaps between:
#   ?view=suggestions (default) — current 4 suggestion cards (S3.insights)
#   ?view=history                — campaign analytics (S3.insights.history)
# Same template renders both — keeps glass nav + header consistent.

@router.get("/insights", response_class=HTMLResponse)
async def insights_page(
    request: Request,
    view: str = "suggestions",
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    if view not in {"suggestions", "history"}:
        view = "suggestions"

    suggestions = []
    funnel = {"sent": 0, "opened": None, "returned": None}
    active_campaigns: list = []
    done_campaigns: list = []

    if view == "suggestions":
        suggestions = await compute_suggestions(db, shop)
    else:
        # 30-day window for the funnel + the campaign list. Campaigns older
        # than this slide off; the dashboard cares about recent performance.
        from app.models import DeeReachCampaign
        thirty_days_ago = utcnow() - timedelta(days=30)
        rows = (await db.exec(
            select(DeeReachCampaign)
            .where(
                DeeReachCampaign.shop_id == shop.id,
                DeeReachCampaign.sent_at.is_not(None),
                DeeReachCampaign.sent_at >= thirty_days_ago,
            )
            .order_by(DeeReachCampaign.sent_at.desc())
        )).all()

        # "Active" = sent within the last 7 days (still earning conversions);
        # older within window = "done". 7d cutoff matches the C5 "ใหม่" window
        # we use elsewhere — feels coherent.
        seven_days_ago = utcnow() - timedelta(days=7)
        kind_th = {
            "win_back": "ชวนลูกค้าหายไปกลับมา",
            "almost_there": "กระตุ้นคนใกล้รับ",
            "unredeemed_reward": "เตือนรางวัลค้าง",
            "new_customer": "ขอบคุณลูกค้าใหม่",
        }
        for r in rows:
            row_dict = {
                "id": str(r.id),
                "kind": r.kind,
                "name": kind_th.get(r.kind, r.kind),
                "sent_at": r.sent_at,
                "audience_count": r.audience_count,
                "credits_spent": r.credits_spent,
            }
            if r.sent_at and r.sent_at >= seven_days_ago:
                active_campaigns.append(row_dict)
            else:
                done_campaigns.append(row_dict)
            funnel["sent"] += r.audience_count

    return templates.TemplateResponse(
        request=request,
        name="shop/insights.html",
        context={
            "shop": shop,
            "view": view,
            "suggestions": suggestions,
            "funnel": funnel,
            "active_campaigns": active_campaigns,
            "done_campaigns": done_campaigns,
        },
    )


# Legacy /shop/deereach links bounce to /shop/insights so old bookmarks
# still land somewhere sensible.
@router.get("/deereach-redirect")
async def insights_legacy_redirect():
    return RedirectResponse(url="/shop/insights", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


# ── S3.customers ────────────────────────────────────────────────────────────
# Full-page customer list — searchable + filter chips. Lives at /shop/customers
# (the "ลูกค้า" tab in the new 4-tab glass nav).

_FILTER_ALL = "all"
_FILTER_REGULAR = "regular"
_FILTER_NEAR = "near"
_FILTER_LAPSED = "lapsed"
_VALID_FILTERS = {_FILTER_ALL, _FILTER_REGULAR, _FILTER_NEAR, _FILTER_LAPSED}

# A customer counts as "ลูกค้าประจำ" once they have ≥ this many visits at the shop.
_REGULAR_VISIT_THRESHOLD = 3
# "ใกล้รับ" surfaces customers who are within this many stamps of the reward.
_NEAR_GAP_MAX = 2
# "หายไป" — last visit longer than this ago.
_LAPSED_DAYS = 14


def _humanize_visit(last_at, now):
    """'มาวันนี้' / 'มาเมื่อวาน' / 'มา N วันก่อน' — shows recency at a glance."""
    if last_at is None:
        return "ยังไม่มีกิจกรรม"
    delta_days = (now.date() - last_at.date()).days
    if delta_days <= 0:
        return "มาวันนี้"
    if delta_days == 1:
        return "มาเมื่อวาน"
    return f"มา {delta_days} วันก่อน"


@router.get("/customers", response_class=HTMLResponse)
async def customers_page(
    request: Request,
    q: Optional[str] = None,
    filter: str = _FILTER_ALL,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """S3.customers — full customer list with search + filter chips.

    Aggregates per-customer stats (visit count, last visit, active points,
    has-claimed-reward) in Python after pulling the raw points/redemptions —
    keeps the SQL simple and fast enough for a single shop's roster, which
    even at 10k customers stays under a few hundred KB.
    """
    if filter not in _VALID_FILTERS:
        filter = _FILTER_ALL

    # Pull every point (non-voided) at this shop for the per-customer rollup.
    point_rows = (await db.exec(
        select(Point.customer_id, Point.created_at, Point.redemption_id)
        .where(Point.shop_id == shop.id, Point.is_voided == False)  # noqa: E712
    )).all()

    # Pull redemptions (non-voided) so we can mark customers who recently
    # claimed a reward — those rows render with the green "✓ รับแล้ว" pill.
    redemption_rows = (await db.exec(
        select(Redemption.customer_id, Redemption.created_at)
        .where(Redemption.shop_id == shop.id, Redemption.is_voided == False)  # noqa: E712
    )).all()

    from app.models import Customer
    customers_by_id = {}
    customer_ids = {p[0] for p in point_rows} | {r[0] for r in redemption_rows}
    if customer_ids:
        customer_rows = (await db.exec(
            select(Customer).where(Customer.id.in_(customer_ids))
        )).all()
        customers_by_id = {c.id: c for c in customer_rows}

    # Build aggregates: for each customer, count visits + earliest-stamp +
    # latest stamp + active stamps (no redemption assigned yet).
    aggs = {}
    for cid, created_at, redemption_id in point_rows:
        a = aggs.setdefault(cid, {"visits": 0, "active": 0, "last_at": None})
        a["visits"] += 1
        if redemption_id is None:
            a["active"] += 1
        if a["last_at"] is None or created_at > a["last_at"]:
            a["last_at"] = created_at

    # Track latest redemption time per customer — drives the "✓ รับแล้ว"
    # pill (only show for redemptions in the last few days).
    latest_redemption = {}
    for cid, created_at in redemption_rows:
        if cid not in latest_redemption or created_at > latest_redemption[cid]:
            latest_redemption[cid] = created_at

    now = utcnow()
    threshold = shop.reward_threshold or 1
    lapsed_cutoff = now - timedelta(days=_LAPSED_DAYS)
    just_claimed_cutoff = now - timedelta(days=1)

    # Active points cap at threshold for display (post-redemption resets):
    # if customer just redeemed, their active_count returned to 0 and
    # claimed status takes priority.
    rows = []
    for cid, agg in aggs.items():
        cust = customers_by_id.get(cid)
        if cust is None:
            continue
        last_redeem = latest_redemption.get(cid)
        just_claimed = last_redeem is not None and last_redeem >= just_claimed_cutoff
        active = agg["active"] if not just_claimed else 0

        # Tag for the row — UI rendering only.
        tag = None
        if active >= threshold:
            tag = "ready"
        elif (threshold - active) <= _NEAR_GAP_MAX and not just_claimed:
            tag = "near"
        elif agg["last_at"] is not None and agg["last_at"] < lapsed_cutoff:
            tag = "lapsed"

        rows.append({
            "id": cid,
            "name": (cust.display_name or "ลูกค้า") if not cust.is_anonymous else "ลูกค้า",
            "initial": ((cust.display_name or "ล")[0]).upper(),
            "visits": agg["visits"],
            "active": min(active, threshold),
            "threshold": threshold,
            "last_visit_str": _humanize_visit(agg["last_at"], now),
            "last_at": agg["last_at"],
            "tag": tag,
            "just_claimed": just_claimed,
        })

    # Apply filter
    if filter == _FILTER_REGULAR:
        rows = [r for r in rows if r["visits"] >= _REGULAR_VISIT_THRESHOLD]
    elif filter == _FILTER_NEAR:
        rows = [r for r in rows if r["tag"] == "near" or r["tag"] == "ready"]
    elif filter == _FILTER_LAPSED:
        rows = [r for r in rows if r["tag"] == "lapsed"]

    # Apply search (case-insensitive name substring)
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in r["name"].lower()]

    # Sort: just-claimed first (delight), then near-ready, then by last_visit desc.
    def sort_key(r):
        priority = 0
        if r["just_claimed"]:
            priority = -3
        elif r["tag"] == "ready":
            priority = -2
        elif r["tag"] == "near":
            priority = -1
        last_at_ts = r["last_at"].timestamp() if r["last_at"] else 0
        return (priority, -last_at_ts)
    rows.sort(key=sort_key)

    return templates.TemplateResponse(
        request=request,
        name="shop/customers.html",
        context={
            "shop": shop,
            "rows": rows,
            "total": len(rows),
            "active_filter": filter,
            "q": q or "",
        },
    )


# ── S10.identity ────────────────────────────────────────────────────────────
# Edit shop name + logo from settings — same logo gen as the onboarding step.

@router.get("/settings/identity", response_class=HTMLResponse)
async def settings_identity_get(
    request: Request,
    gen: int = 0,
    shop: Shop = Depends(get_current_shop),
):
    options = generate_logos(shop.name, seed=gen)
    saved_pick = None
    if shop.logo_url and shop.logo_url.startswith("text:"):
        sid = shop.logo_url[5:]
        if sid in VALID_STYLE_IDS and sid not in {o["id"] for o in options}:
            saved_pick = render_style(shop.name, sid)
    return templates.TemplateResponse(
        request=request,
        name="shop/settings/identity.html",
        context={"shop": shop, "options": options, "saved_pick": saved_pick, "next_gen": gen + 1},
    )


@router.post("/settings/identity")
async def settings_identity_post(
    name: str = Form(...),
    logo_choice: Optional[str] = Form(None),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    shop.name = name.strip() or shop.name
    if logo_choice in VALID_STYLE_IDS:
        shop.logo_url = f"text:{logo_choice}"
    db.add(shop)
    await db.commit()
    return RedirectResponse(url="/shop/settings", status_code=status.HTTP_303_SEE_OTHER)


# ── S10.location ────────────────────────────────────────────────────────────
# Province (existing Shop.location) + district + address detail.

@router.get("/settings/location", response_class=HTMLResponse)
async def settings_location_get(request: Request, shop: Shop = Depends(get_current_shop)):
    return templates.TemplateResponse(
        request=request,
        name="shop/settings/location.html",
        context={"shop": shop},
    )


@router.post("/settings/location")
async def settings_location_post(
    province: Optional[str] = Form(None),
    district: Optional[str] = Form(None),
    address_detail: Optional[str] = Form(None),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    shop.location = (province or "").strip() or None
    shop.district = (district or "").strip() or None
    shop.address_detail = (address_detail or "").strip() or None
    db.add(shop)
    await db.commit()
    return RedirectResponse(url="/shop/settings", status_code=status.HTTP_303_SEE_OTHER)


# ── S10.contact ─────────────────────────────────────────────────────────────
# Public shop phone + per-day opening hours.

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@router.get("/settings/contact", response_class=HTMLResponse)
async def settings_contact_get(request: Request, shop: Shop = Depends(get_current_shop)):
    # Hydrate hours with defaults so the template can render every day even
    # for shops that haven't saved yet. Default = closed.
    saved = shop.opening_hours or {}
    hours = {
        d: {
            "open": saved.get(d, {}).get("open", "07:00"),
            "close": saved.get(d, {}).get("close", "18:00"),
            "closed": saved.get(d, {}).get("closed", d == "sun"),
        }
        for d in _DAYS
    }
    day_th = {
        "mon": "จันทร์", "tue": "อังคาร", "wed": "พุธ", "thu": "พฤหัสบดี",
        "fri": "ศุกร์", "sat": "เสาร์", "sun": "อาทิตย์",
    }
    return templates.TemplateResponse(
        request=request,
        name="shop/settings/contact.html",
        context={"shop": shop, "hours": hours, "day_th": day_th, "days": _DAYS},
    )


@router.post("/settings/contact")
async def settings_contact_post(
    request: Request,
    shop_phone: Optional[str] = Form(None),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """Save contact info. Form fields per day: `mon_open`, `mon_close`,
    `mon_closed` (checkbox). Build the JSON map server-side."""
    form = await request.form()
    hours = {}
    for d in _DAYS:
        closed = form.get(f"{d}_closed") == "1"
        hours[d] = {
            "open": (form.get(f"{d}_open") or "").strip() or "07:00",
            "close": (form.get(f"{d}_close") or "").strip() or "18:00",
            "closed": closed,
        }
    shop.shop_phone = (shop_phone or "").strip() or None
    shop.opening_hours = hours
    db.add(shop)
    await db.commit()
    return RedirectResponse(url="/shop/settings", status_code=status.HTTP_303_SEE_OTHER)


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
