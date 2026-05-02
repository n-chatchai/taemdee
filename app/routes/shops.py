import io
import uuid
from datetime import timedelta, timezone
from typing import Optional

import segno
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from app.core.templates import templates
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_shop
from app.core.database import get_session
from app.models import Redemption, Shop, Point
from app.models.util import utcnow
from app.routes.auth import _set_session_cookie
from app.services.auth import issue_session_token
from app.services.branch import s3_top_context
from app.services.deereach import compute_suggestions
from app.services.events import stream as event_stream
from app.services.items import ItemError, claim as claim_item, list_available as list_available_items
from app.services.logo_gen import VALID_STYLE_IDS, generate_logos, render_style
from app.services.referrals import (
    complete_referral_for,
    create_referral_code,
    find_referral_by_code,
)
from app.services.storage import upload_to_r2

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
        name="shop/login.html",
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
    period: str = "today",
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    if not shop.is_onboarded:
        return RedirectResponse(url="/shop/onboard", status_code=status.HTTP_303_SEE_OTHER)

    if period not in ("today", "week", "month"):
        period = "today"

    now = utcnow()
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)
    month_ago = now - timedelta(days=30)
    two_months_ago = now - timedelta(days=60)

    # Today / yesterday slices for the snapshot card. Day boundaries use
    # Bangkok local time so a 23:55 stamp doesn't get filed under "yesterday"
    # when the owner glances at the dashboard at 00:05.
    from app.models.util import BKK
    bkk_now = now.replace(tzinfo=timezone.utc).astimezone(BKK)
    today_start_bkk = bkk_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_bkk.astimezone(timezone.utc).replace(tzinfo=None)
    yesterday_start_utc = today_start_utc - timedelta(days=1)

    week_start_utc = today_start_utc - timedelta(days=6)

    # ── ONE SQL roundtrip for every Point-derived stat. Aggregate counters
    # use FILTER (Postgres + SQLite ≥ 3.30) so we collect 8 numbers in a
    # single scan of the points table instead of 8 sequential queries. The
    # 7-day created_at list still needs a separate fetch for the bucketed
    # trend bars (group-by + array_agg would also work but adds DB-specific
    # SQL — keep it portable).
    point_stats = (await db.exec(
        select(
            func.count(func.distinct(Point.customer_id)).filter(
                Point.created_at >= today_start_utc
            ).label("customers_today"),
            func.count(func.distinct(Point.customer_id)).filter(
                Point.created_at >= yesterday_start_utc,
                Point.created_at < today_start_utc,
            ).label("customers_yesterday"),
            func.count(func.distinct(Point.customer_id)).filter(
                Point.created_at >= week_ago
            ).label("customers_this_week"),
            func.count(func.distinct(Point.customer_id)).filter(
                Point.created_at >= two_weeks_ago,
                Point.created_at < week_ago,
            ).label("customers_last_week"),
            func.count(func.distinct(Point.customer_id)).filter(
                Point.created_at >= month_ago
            ).label("customers_this_month"),
            func.count(func.distinct(Point.customer_id)).filter(
                Point.created_at >= two_months_ago,
                Point.created_at < month_ago,
            ).label("customers_last_month"),
            func.count().filter(
                Point.created_at >= today_start_utc
            ).label("points_today"),
            func.count().filter(
                Point.created_at >= week_ago
            ).label("points_this_week"),
            func.count().filter(
                Point.created_at >= two_weeks_ago,
                Point.created_at < week_ago,
            ).label("points_last_week"),
            func.count().label("points_total"),
        )
        .select_from(Point)
        .where(Point.shop_id == shop.id, Point.is_voided == False)  # noqa: E712
    )).one()
    customers_today = point_stats[0]
    customers_yesterday = point_stats[1]
    customers_this_week = point_stats[2]
    customers_last_week = point_stats[3]
    customers_this_month = point_stats[4]
    customers_last_month = point_stats[5]
    points_today = point_stats[6]
    trend_total = point_stats[7]
    prev_week_total = point_stats[8]
    points_total = point_stats[9]
    today_delta = customers_today - customers_yesterday
    wow_delta = customers_this_week - customers_last_week
    mom_delta = customers_this_month - customers_last_month
    wow_pct = (
        int(round(100 * (trend_total - prev_week_total) / prev_week_total))
        if prev_week_total else None
    )

    # Period-aware display values for the big number + delta line. The
    # trend chart below stays "last 7 days" regardless of period — design
    # only swaps the headline metric when the pill changes.
    if period == "week":
        period_count = customers_this_week
        period_delta = wow_delta
        period_delta_label = "จากสัปดาห์ก่อน"
    elif period == "month":
        period_count = customers_this_month
        period_delta = mom_delta
        period_delta_label = "จากเดือนก่อน"
    else:
        period_count = customers_today
        period_delta = today_delta
        period_delta_label = "จากเมื่อวาน"

    # ── ONE SQL roundtrip for the Redemption side: today + total in a
    # single scan. Same FILTER pattern as the points aggregate above.
    redemption_stats = (await db.exec(
        select(
            func.count().filter(
                Redemption.created_at >= today_start_utc
            ).label("redemptions_today"),
            func.count().label("redemptions_total"),
        )
        .select_from(Redemption)
        .where(Redemption.shop_id == shop.id, Redemption.is_voided == False)  # noqa: E712
    )).one()
    redemptions_today = redemption_stats[0]
    redemptions_total = redemption_stats[1]

    # 7-day buckets for the trend chart — one query per surface (points
    # for the orange scan bar, redemptions for the ink overlay) so we
    # can render the design's stacked tb-bar + tb-redeem.
    point_rows = (await db.exec(
        select(Point.created_at)
        .where(
            Point.shop_id == shop.id,
            Point.created_at >= week_start_utc,
            Point.is_voided == False,  # noqa: E712
        )
    )).all()
    redemption_rows = (await db.exec(
        select(Redemption.created_at)
        .where(
            Redemption.shop_id == shop.id,
            Redemption.created_at >= week_start_utc,
            Redemption.is_voided == False,  # noqa: E712
        )
    )).all()
    daily_counts = [0] * 7
    daily_redemptions = [0] * 7
    today_bkk_date = today_start_bkk.date()
    for created_at in point_rows:
        bkk_date = created_at.replace(tzinfo=timezone.utc).astimezone(BKK).date()
        offset_days = (today_bkk_date - bkk_date).days
        if 0 <= offset_days < 7:
            daily_counts[6 - offset_days] += 1
    for created_at in redemption_rows:
        bkk_date = created_at.replace(tzinfo=timezone.utc).astimezone(BKK).date()
        offset_days = (today_bkk_date - bkk_date).days
        if 0 <= offset_days < 7:
            daily_redemptions[6 - offset_days] += 1
    max_daily = max(daily_counts) or 1
    daily_pct = [int(round(100 * c / max_daily)) for c in daily_counts]
    # Redeem overlay is a share of the same day's stamps, so percentages
    # divide against `daily_counts[i]`, not `max_daily`. Days with zero
    # stamps stay at 0 (no overlay).
    daily_redeem_pct = [
        int(round(100 * r / daily_counts[i])) if daily_counts[i] else 0
        for i, r in enumerate(daily_redemptions)
    ]
    # Day labels aligned to today: the rightmost bar IS today, so offset
    # from today's BKK weekday backward by (6 - i). Drops the broken
    # fixed-sequence formula that ignored what day it actually was.
    _DAY_LABELS_TH = ("จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา.")
    today_weekday = today_bkk_date.weekday()
    daily_labels = [
        _DAY_LABELS_TH[(today_weekday - (6 - i)) % 7] for i in range(7)
    ]

    # Branches count + first branch name in a single roundtrip via window
    # function. Most shops have 1 branch — `branch_label` only shows on
    # the day-caption row when count > 1.
    s3_top = await s3_top_context(db, shop, now=now)

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

    # Generic claim cards (welcome credit, future onboarding nudges, etc.)
    items = await list_available_items(db, shop)

    return templates.TemplateResponse(
        request=request,
        name="shop/dashboard.html",
        context={
            "shop": shop,
            "period": period,
            "period_count": period_count,
            "period_delta": period_delta,
            "period_delta_label": period_delta_label,
            "customers_today": customers_today,
            "today_delta": today_delta,
            "points_today": points_today,
            "redemptions_today": redemptions_today,
            "customers_this_week": customers_this_week,
            "wow_delta": wow_delta,
            "wow_pct": wow_pct,
            "daily_pct": daily_pct,
            "daily_counts": daily_counts,
            "daily_redemptions": daily_redemptions,
            "daily_redeem_pct": daily_redeem_pct,
            "daily_labels": daily_labels,
            "points_total": points_total,
            "redemptions_total": redemptions_total,
            "feed_cap": app_settings.shop_customer_last_scan_display_number,
            "attn_cards": attn_cards,
            "items": items,
            **s3_top,
        },
    )


@router.post("/items/{kind}/claim")
async def claim_dashboard_item(
    kind: str,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """Apply the per-kind side effect (e.g. credit grant), record the
    ShopItem row, redirect back to /shop/dashboard. Already-claimed +
    unknown kinds → 400 with the Thai error so the user sees what
    happened instead of a silent retry."""
    try:
        await claim_item(db, shop, kind)
    except ItemError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return RedirectResponse(url="/shop/dashboard", status_code=status.HTTP_303_SEE_OTHER)


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
    
    picked_id = None
    custom_text = None
    if shop.logo_url and shop.logo_url.startswith("text:"):
        parts = shop.logo_url.split(":", 2)
        picked_id = parts[1] if len(parts) > 1 else None
        custom_text = parts[2].strip() if len(parts) == 3 else None

    saved_pick = None
    if picked_id and picked_id not in {o["id"] for o in options} and picked_id in VALID_STYLE_IDS:
        saved_pick = render_style(shop.name, picked_id)
        if custom_text:
            saved_pick["text"] = custom_text

    from app.services.thai_address import all_districts, district_province_pairs, lookup_provinces
    initial_candidates = lookup_provinces(shop.district) if shop.district else []
    initial_province = (
        shop.location if shop.location in initial_candidates
        else (initial_candidates[0] if len(initial_candidates) == 1 else None)
    )
    return templates.TemplateResponse(
        request=request,
        name="shop/onboard/identity.html",
        context={
            "shop": shop,
            "options": options,
            "saved_pick": saved_pick,
            "gen": gen,
            "next_gen": gen + 1,
            "picked_id": picked_id,
            "custom_text": custom_text or "",
            "logos_partial_url": "/shop/onboard/identity/logos_partial",
            "all_districts": all_districts(),
            "district_pairs": district_province_pairs(),
            "initial_province": initial_province,
            "initial_candidates": initial_candidates,
        },
    )

@router.get("/onboard/identity/logos_partial", response_class=HTMLResponse)
async def onboard_identity_logos_partial(
    request: Request,
    name: str,
    gen: int = 0,
    shop: Shop = Depends(get_current_shop),
):
    """HTMX endpoint to re-generate logos on the fly as the user types the shop name."""
    options = generate_logos(name, seed=gen)
    
    # We only show generated options here. If they had a saved pick, 
    # it gets overwritten if they change the name and pick a new one.
    # To keep it simple, we just return the new options.
    return templates.TemplateResponse(
        request=request,
        name="_partials/logo_picker.html",
        context={
            "shop": shop,
            "shop_name": name,
            "options": options,
            "saved_pick": None,
            "picked_id": options[0]["id"] if options else None,
            "custom_text": "",
            "next_gen": gen + 1,
            "logos_partial_url": "/shop/onboard/identity/logos_partial",
        },
    )


@router.get("/onboard/district/lookup")
async def onboard_district_lookup(q: str = ""):
    """JSON endpoint the S2.1 picker pings on input — always returns
    a `provinces` list. 0 = no match, 1 = auto-fill chip, N>1 = render
    the disambiguation picker (e.g., จอมทอง → กรุงเทพมหานคร / เชียงใหม่)."""
    from app.services.thai_address import lookup_provinces
    return JSONResponse({"provinces": lookup_provinces(q)})


@router.post("/onboard/identity")
async def onboard_identity_post(
    request: Request,
    name: str = Form(...),
    logo_choice: Optional[str] = Form(None),
    custom_text: Optional[str] = Form(None),
    province: Optional[str] = Form(None),
    district: Optional[str] = Form(None),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    from app.services.thai_address import lookup_provinces
    cleaned_name = (name or "").strip() or shop.name
    cleaned_district = (district or "").strip()
    cleaned_province = (province or "").strip()
    # Province lookup hierarchy:
    #   1. explicit `province` from form (used for ambiguous picks +
    #      free-text fallback for districts not in the dataset)
    #   2. derived from district (only when exactly 1 candidate)
    if not cleaned_province and cleaned_district:
        candidates = lookup_provinces(cleaned_district)
        if len(candidates) == 1:
            cleaned_province = candidates[0]
        # If len > 1 (จอมทอง, เฉลิมพระเกียรติ) the user must pick on the
        # picker; we leave province empty and the form would re-render with
        # the picker open. The submit-without-pick case is rare enough that
        # we just save with empty province and let the user fix in S10.

    # Collision check (S2.1.warn): same name + same district = block. Same
    # name + DIFFERENT district = silent auto-suffix so the customer can
    # disambiguate ("ลุงหมี · นิมมาน" vs "ลุงหมี · ทุ่งโฮเต็ล"). Anonymous
    # / pre-naming shops carrying the seed name are excluded so the owner
    # can keep their auto-generated default through this step.
    same_name_clause = func.lower(Shop.name) == cleaned_name.lower()
    other_shops = (await db.exec(
        select(Shop).where(same_name_clause, Shop.id != shop.id)
    )).all()
    same_district_collision = next(
        (s for s in other_shops if cleaned_district and (s.district or "").lower() == cleaned_district.lower()),
        None,
    )
    different_district_collision = next(
        (s for s in other_shops if not cleaned_district or (s.district or "").lower() != cleaned_district.lower()),
        None,
    )

    if same_district_collision is not None:
        # Re-render the same step with inline warning + form values preserved.
        from app.services.logo_gen import generate_logos, render_style
        from app.services.thai_address import all_districts, district_province_pairs
        shop.name = cleaned_name
        shop.district = cleaned_district or shop.district
        if cleaned_province:
            shop.location = cleaned_province
        options = generate_logos(cleaned_name, seed=0)
        return templates.TemplateResponse(
            request=request,
            name="shop/onboard/identity.html",
            context={
                "shop": shop,
                "options": options,
                "saved_pick": None,
                "next_gen": 1,
                "picked_id": None,
                "custom_text": "",
                "logos_partial_url": "/shop/onboard/identity/logos_partial",
                "all_districts": all_districts(),
                "district_pairs": district_province_pairs(),
                "initial_province": cleaned_province or None,
                "initial_candidates": [],
                "collision_warning": {
                    "district": cleaned_district,
                    "suggestion": f"{cleaned_name} 2",
                },
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Different-district collision → auto-suffix so the customer doesn't see
    # two identical names on /my-cards. Skip if district is blank (we can't
    # disambiguate without one).
    if different_district_collision is not None and cleaned_district and not cleaned_name.endswith(f"· {cleaned_district}"):
        cleaned_name = f"{cleaned_name} · {cleaned_district}"

    shop.name = cleaned_name
    if logo_choice and logo_choice.startswith("url:"):
        shop.logo_url = logo_choice
    elif logo_choice in VALID_STYLE_IDS:
        safe_custom_text = (custom_text or "").strip().replace(":", "-")
        if safe_custom_text:
            shop.logo_url = f"text:{logo_choice}:{safe_custom_text}"
        else:
            shop.logo_url = f"text:{logo_choice}"
    if cleaned_province:
        shop.location = cleaned_province
    if cleaned_district:
        shop.district = cleaned_district
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
    if reward_image:
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
    if reward_image:
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
async def shop_events(request: Request):
    """Server-Sent Events stream for the shop dashboard's live feed.

    DOES NOT use Depends(get_current_shop) — that pulls a yield-based
    DB session via Depends(get_session), which FastAPI keeps alive for
    the entire StreamingResponse. With ~4 gunicorn workers each
    holding a session per open dashboard tab, the asyncpg pool was
    exhausting. Decode the session cookie + look up the shop with a
    short-lived manual session, release it, then start the stream."""
    from app.core.auth import SESSION_COOKIE_NAME
    from app.core.database import SessionFactory
    from app.services.auth import decode_session_token

    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    payload = decode_session_token(cookie) if cookie else None
    shop_id_str = payload.get("shop_id") if payload else None
    if not shop_id_str:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session missing")
    try:
        shop_id = uuid.UUID(shop_id_str)
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session shop id malformed")

    async with SessionFactory() as db:
        shop = await db.get(Shop, shop_id)
        if not shop:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session shop missing")

    return StreamingResponse(
        event_stream(shop_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    from app.models import ShopMenuItem
    await db.refresh(shop, ["branches"])
    menu_count = (await db.exec(
        select(func.count())
        .select_from(ShopMenuItem)
        .where(ShopMenuItem.shop_id == shop.id)
    )).one()
    return templates.TemplateResponse(
        request=request,
        name="shop/settings.html",
        context={"shop": shop, "menu_count": menu_count},
    )


# ── S3.insights ─────────────────────────────────────────────────────────────
# "แต้มดีแนะนำ" hub. Default view is the suggestion list with a brief
# 30-day metrics card on top that links to ?view=history (S3.insights.history),
# the full performance page with funnel + campaign list. Same template
# renders both — keeps the glass nav + header consistent.

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
    # opened/returned + their pcts stay None until the LINE Messaging API
    # tracking ships — template renders '—' / hides the percent badge.
    funnel = {
        "sent": 0,
        "opened": None,
        "returned": None,
        "opened_pct": None,
        "returned_pct": None,
    }
    active_campaigns: list = []
    done_campaigns: list = []

    # 30-day campaign rollup — needed by both views (brief card on
    # suggestions, full funnel + campaign list on history).
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
        "manual": "แคมเปญของคุณเอง",
    }
    for r in rows:
        # Per DeeReach v2: credits live in satang (1 credit = 100 satang).
        # Use final_ if the campaign actually sent, else fall back to the
        # reservation amount the engine locked when scheduling.
        spent_satang = r.final_credits_satang or r.locked_credits_satang
        row_dict = {
            "id": str(r.id),
            "kind": r.kind,
            "name": kind_th.get(r.kind, r.kind),
            "sent_at": r.sent_at,
            "audience_count": r.audience_count,
            "credits_spent": spent_satang // 100,
        }
        if r.sent_at and r.sent_at >= seven_days_ago:
            active_campaigns.append(row_dict)
        else:
            done_campaigns.append(row_dict)
        funnel["sent"] += r.audience_count

    if view == "suggestions":
        suggestions = await compute_suggestions(db, shop)

    s3_top = await s3_top_context(db, shop)
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
            **s3_top,
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

    s3_top = await s3_top_context(db, shop, now=now)
    return templates.TemplateResponse(
        request=request,
        name="shop/customers.html",
        context={
            "shop": shop,
            "rows": rows,
            "total": len(rows),
            "active_filter": filter,
            "q": q or "",
            **s3_top,
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
    
    picked_id = None
    custom_text = None
    if shop.logo_url and shop.logo_url.startswith("text:"):
        parts = shop.logo_url.split(":", 2)
        picked_id = parts[1] if len(parts) > 1 else None
        custom_text = parts[2].strip() if len(parts) == 3 else None

    saved_pick = None
    if picked_id and picked_id not in {o["id"] for o in options} and picked_id in VALID_STYLE_IDS:
        saved_pick = render_style(shop.name, picked_id)
        if custom_text:
            saved_pick["text"] = custom_text

    return templates.TemplateResponse(
        request=request,
        name="shop/settings/identity.html",
        context={
            "shop": shop,
            "options": options,
            "saved_pick": saved_pick,
            "next_gen": gen + 1,
            "picked_id": picked_id,
            "custom_text": custom_text or "",
            "logos_partial_url": "/shop/settings/identity/logos_partial",
        },
    )

@router.get("/settings/identity/logos_partial", response_class=HTMLResponse)
async def settings_identity_logos_partial(
    request: Request,
    name: str,
    gen: int = 0,
    shop: Shop = Depends(get_current_shop),
):
    """HTMX endpoint to re-generate logos on the fly."""
    options = generate_logos(name, seed=gen)
    return templates.TemplateResponse(
        request=request,
        name="_partials/logo_picker.html",
        context={
            "shop": shop,
            "shop_name": name,
            "options": options,
            "saved_pick": None,
            "picked_id": options[0]["id"] if options else None,
            "next_gen": gen + 1,
            "logos_partial_url": "/shop/settings/identity/logos_partial",
        },
    )


@router.post("/settings/identity")
async def settings_identity_post(
    name: str = Form(...),
    logo_choice: Optional[str] = Form(None),
    custom_text: Optional[str] = Form(None),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    shop.name = name.strip() or shop.name
    if logo_choice and logo_choice.startswith("url:"):
        shop.logo_url = logo_choice
    elif logo_choice in VALID_STYLE_IDS:
        safe_custom_text = (custom_text or "").strip().replace(":", "-")
        if safe_custom_text:
            shop.logo_url = f"text:{logo_choice}:{safe_custom_text}"
        else:
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


@router.get("/settings/story", response_class=HTMLResponse)
async def settings_story_get(request: Request, shop: Shop = Depends(get_current_shop)):
    """S10.story — owner editor for the C9 emotional layer. Two textareas:
    `thanks_message` (short personal note) and `story_text` (longer paragraph)."""
    return templates.TemplateResponse(
        request=request,
        name="shop/settings/story.html",
        context={"shop": shop},
    )


@router.post("/settings/story")
async def settings_story_post(
    thanks_message: Optional[str] = Form(None),
    story_text: Optional[str] = Form(None),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    shop.thanks_message = (thanks_message or "").strip() or None
    shop.story_text = (story_text or "").strip() or None
    db.add(shop)
    await db.commit()
    return RedirectResponse(url="/shop/settings", status_code=status.HTTP_303_SEE_OTHER)


# Menu emoji palette — small curated set so the form stays a one-tap
# picker. Aligned with Thai SME categories (food, drink, dessert).
_MENU_EMOJI_PALETTE = (
    "☕", "🍵", "🧋", "🥐",
    "🍰", "🍩", "🍞", "🥗",
    "🍜", "🍚", "🍕", "🌮",
    "🍔", "🍦", "🍪", "🥟",
)


@router.get("/settings/menu", response_class=HTMLResponse)
async def settings_menu_get(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """S10.menu — owner editor for เมนูเด็ด. Lists existing items in
    sort_order, plus a single add form. Edit/delete are inline POST
    forms per row (consistent with the other shop settings pages —
    no JS-driven CRUD)."""
    from app.models import ShopMenuItem
    rows = (await db.exec(
        select(ShopMenuItem)
        .where(ShopMenuItem.shop_id == shop.id)
        .order_by(ShopMenuItem.sort_order, ShopMenuItem.created_at)
    )).all()
    return templates.TemplateResponse(
        request=request,
        name="shop/settings/menu.html",
        context={
            "shop": shop,
            "items": list(rows),
            "emoji_palette": _MENU_EMOJI_PALETTE,
        },
    )


@router.post("/settings/menu")
async def settings_menu_create(
    name: str = Form(...),
    price: Optional[str] = Form(None),
    emoji: Optional[str] = Form(None),
    is_signature: Optional[str] = Form(None),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """Add a new menu item. New items append at the end (sort_order =
    max + 1) so the form stays a single bottom add — no drag-to-reorder
    yet."""
    from app.models import ShopMenuItem
    name = (name or "").strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ชื่อเมนูห้ามว่าง")
    parsed_price: Optional[int] = None
    if price and (s := price.strip()):
        try:
            parsed_price = max(0, int(s))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "ราคาต้องเป็นตัวเลข")
    next_order = (await db.exec(
        select(func.coalesce(func.max(ShopMenuItem.sort_order), -1) + 1)
        .where(ShopMenuItem.shop_id == shop.id)
    )).one()
    item = ShopMenuItem(
        shop_id=shop.id,
        name=name,
        price=parsed_price,
        emoji=(emoji or "").strip() or None,
        is_signature=is_signature == "on",
        sort_order=int(next_order),
    )
    db.add(item)
    await db.commit()
    return RedirectResponse(
        url="/shop/settings/menu", status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/settings/menu/{item_id}/delete")
async def settings_menu_delete(
    item_id: uuid.UUID,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    from app.models import ShopMenuItem
    item = await db.get(ShopMenuItem, item_id)
    if not item or item.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบเมนูนี้")
    await db.delete(item)
    await db.commit()
    return RedirectResponse(
        url="/shop/settings/menu", status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/settings/menu/{item_id}/signature")
async def settings_menu_toggle_signature(
    item_id: uuid.UUID,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """Flip is_signature for one item. The "ขายดีที่สุด" tag in
    shop.story has no per-shop limit yet — the design just paints the
    overlay on whichever items are flagged."""
    from app.models import ShopMenuItem
    item = await db.get(ShopMenuItem, item_id)
    if not item or item.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบเมนูนี้")
    item.is_signature = not item.is_signature
    db.add(item)
    await db.commit()
    return RedirectResponse(
        url="/shop/settings/menu", status_code=status.HTTP_303_SEE_OTHER,
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


@router.get("/qr/live", response_class=HTMLResponse)
async def shop_qr_live(
    request: Request,
    shop: Shop = Depends(get_current_shop),
):
    """S3.qr — fullscreen rotating-QR mode for "หันจอให้ลูกค้าสแกน".
    QR rotates every 15s with a signed JWT in the URL; screenshots of
    stale QRs hit the expired-token branch in /scan/{shop_id}."""
    from app.services.auth import LIVE_QR_TTL_SECONDS, issue_live_qr_token
    token = issue_live_qr_token(shop.id)
    base = str(request.base_url).rstrip("/")
    scan_url = f"{base}/scan/{shop.id}?t={token}"
    qr_svg = segno.make(scan_url, error="m").svg_inline(
        scale=10, dark="#111111", light="#ffffff", border=1, omitsize=True
    )
    return templates.TemplateResponse(
        request=request,
        name="shop/qr_live.html",
        context={
            "shop": shop,
            "qr_svg": qr_svg,
            "ttl_seconds": LIVE_QR_TTL_SECONDS,
        },
    )


@router.get("/qr/live/refresh")
async def shop_qr_live_refresh(
    request: Request,
    shop: Shop = Depends(get_current_shop),
):
    """JSON endpoint the S3.qr page polls every 15s — returns a fresh
    QR SVG + the new TTL countdown. Lets the page swap innerHTML
    without a full reload."""
    from app.services.auth import LIVE_QR_TTL_SECONDS, issue_live_qr_token
    token = issue_live_qr_token(shop.id)
    base = str(request.base_url).rstrip("/")
    scan_url = f"{base}/scan/{shop.id}?t={token}"
    qr_svg = segno.make(scan_url, error="m").svg_inline(
        scale=10, dark="#111111", light="#ffffff", border=1, omitsize=True
    )
    return JSONResponse({"svg": qr_svg, "expires_in": LIVE_QR_TTL_SECONDS})


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

@router.post("/upload/logo")
async def upload_logo(
    file: UploadFile = File(...),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """Uploads a custom shop logo to R2."""
    content = await file.read()
    url = await upload_to_r2(
        content,
        file.filename,
        file.content_type,
        folder=f"shops/{shop.id}/logos",
        is_image=True
    )
    if not url:
        raise HTTPException(status_code=500, detail="Upload failed")
    
    shop.logo_url = f"url:{url}"
    db.add(shop)
    await db.commit()
    
    return {"url": url}


@router.post("/upload/reward")
async def upload_reward_image(
    file: UploadFile = File(...),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """Uploads a custom reward image to R2."""
    content = await file.read()
    url = await upload_to_r2(
        content,
        file.filename,
        file.content_type,
        folder="reward_images",
        is_image=True
    )
    if not url:
        raise HTTPException(status_code=500, detail="Upload failed")
    
    shop.reward_image = url
    db.add(shop)
    await db.commit()
    
    return {"url": url}
