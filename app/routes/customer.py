"""Customer-facing routes: scan to get a stamp, view DeeCard, Soft Wall claim."""

import uuid
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from app.core.templates import templates
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import (
    CUSTOMER_COOKIE_NAME,
    find_or_create_customer,
    set_customer_cookie,
)
from app.core.database import get_session
from app.models import Branch, Customer, Inbox, Redemption, Shop, Point
from app.models.util import BKK, bkk_feed_time, utcnow
from app.services.auth import verify_otp
from app.services.events import feed_row_html, publish
from app.services.issuance import IssuanceError, issue_point
from app.services.pdpa import delete_customer_account
from app.services.recovery import ensure_recovery_code, find_by_code
from app.services.redemption import RedemptionError, active_point_count, redeem
from app.services.soft_wall import claim_by_phone


async def publish_gifts_update(customer_id: uuid.UUID) -> None:
    from app.services.events import publish_customer
    from app.core.database import SessionFactory
    try:
        async with SessionFactory() as db:
            count = await _active_gifts_count(db, customer_id)
            publish_customer(customer_id, "gifts-update", str(count))
    except Exception:
        pass


router = APIRouter()


@router.get("/sse/me")
async def customer_event_stream(
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
):
    """Per-customer SSE stream — drives the dock badges live (a DeeReach
    inbox drop, a fresh redemption, or a tap on "ใช้" each ripples the
    relevant tab count without a refresh). Event kinds:
        inbox-update — payload is the new unread count as plain string.
        gifts-update — payload is the new active-voucher count as plain string.
    Anonymous customers get a stream too (cookie alone identifies them) so
    these updates still reach guest mode.

    DOES NOT use Depends(get_session) — for StreamingResponse,
    FastAPI keeps yield-based dependencies alive for the entire stream
    lifetime, which on /sse/me is "until the customer closes the tab."
    Each open tab would hold an asyncpg connection, exhausting the
    pool under modest load. Open + close a short-lived session here
    instead, then start the stream with no DB ties."""
    from app.core.database import SessionFactory
    from app.services.events import stream_customer

    async with SessionFactory() as db:
        customer, was_created = await find_or_create_customer(customer_cookie, db)
        customer_id = customer.id

    response = StreamingResponse(
        stream_customer(customer_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    if was_created:
        set_customer_cookie(response, customer_id)
    return response


async def _inbox_unread_count(db: AsyncSession, customer_id: uuid.UUID) -> int:
    """Total unread Inbox rows for this customer — used to drive the
    `ข้อความ` tab badge on the customer dock. Cheap single-row count."""
    return (await db.exec(
        select(func.count())
        .select_from(Inbox)
        .where(Inbox.customer_id == customer_id, Inbox.read_at.is_(None))
    )).one()


async def _active_gifts_count(db: AsyncSession, customer_id: uuid.UUID) -> int:
    """Active (unredeemed-by-shop, not voided) Redemption rows for this
    customer — drives the `ของขวัญ` tab badge on the customer dock so a
    fresh redeem (or a tap on "ใช้") updates the count without a refresh."""
    return (await db.exec(
        select(func.count())
        .select_from(Redemption)
        .where(
            Redemption.customer_id == customer_id,
            Redemption.served_at.is_(None),
            Redemption.is_voided == False,  # noqa: E712
        )
    )).one()


@router.get("/customer/login", response_class=HTMLResponse)
async def customer_login_page(request: Request):
    """Standalone customer login page based on the shop's S1 design."""
    return templates.TemplateResponse(
        request=request,
        name="customer_login.html",
        context={},
    )


@router.post("/customer/login")
async def customer_dev_login(
    phone: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    """DEV shortcut for customer login (matches the shop's dev_login behavior)."""
    # Find or create by phone
    result = await db.exec(select(Customer).where(Customer.phone == phone))
    customer = result.first()
    if not customer:
        customer = Customer(phone=phone, display_name="คุณลูกค้า")
        db.add(customer)
        await db.commit()
        await db.refresh(customer)

    response = RedirectResponse(url="/my-cards", status_code=status.HTTP_303_SEE_OTHER)
    set_customer_cookie(response, customer.id)
    return response


# Literal /card/* paths — must be registered BEFORE /card/{shop_id} so FastAPI
# doesn't try to parse "save", "account" etc. as a UUID and 422.
@router.get("/card/save", response_class=HTMLResponse)
async def soft_wall_page(request: Request, next_redeem: Optional[str] = None):
    """C3 — Soft Wall standalone page: claim by phone OTP. The optional
    `?next_redeem=<shop_id>` query is forwarded into the claim POST so the
    server can fire the redemption immediately after the OTP succeeds
    (auto-resume from the C4 gate)."""
    return templates.TemplateResponse(
        request=request,
        name="card_save.html",
        context={"next_redeem": next_redeem},
    )


def _mask_phone(phone: Optional[str]) -> str:
    """+66 89 ••• 4523 — show country prefix and last 4, mask the middle."""
    if not phone:
        return ""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if digits.startswith("66") and len(digits) >= 11:
        return f"+66 {digits[2:4]} ••• {digits[-4:]}"
    if digits.startswith("0") and len(digits) >= 9:
        return f"+66 {digits[1:3]} ••• {digits[-4:]}"
    return phone


# cards.list — small palette swatches the .cl-mini / .cl-other tiles
# tint themselves with via inline `--shop-color`. Picked from the
# design mockup (warm earthy hues that work on the cream surface) and
# rotated by hash of the shop id so the same shop keeps the same
# colour across page loads without needing a stored field.
_SHOP_SWATCHES = (
    "#E87A6A",  # coral
    "#3E6B5A",  # forest mint
    "#C49A4D",  # mustard
    "#7A4A8A",  # plum
    "#3A6B7A",  # teal
    "#B05F4A",  # brick
    "#5C7A8A",  # slate
)


def _shop_swatch(shop_id: uuid.UUID) -> str:
    return _SHOP_SWATCHES[shop_id.int % len(_SHOP_SWATCHES)]


@router.get("/card/account", response_class=HTMLResponse)
async def account_menu(
    request: Request,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """C6 — customer account menu (profile, my-stuff, settings, logout, delete).
    Anonymous customers see the same screen — the template degrades to "ลูกค้า
    แต้มดี" without phone/LINE meta. Forcing them to /card/save instead made
    the gear icon unusable for guests who hadn't signed up yet, even though
    the only personal control they need (text size, notifications) lives
    here regardless of claim status."""
    customer, _ = await find_or_create_customer(customer_cookie, db)

    from app.models.util import BKK
    from datetime import datetime, timezone
    weekday_th = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")[
        datetime.now(timezone.utc).astimezone(BKK).weekday()
    ]

    return templates.TemplateResponse(
        request=request,
        name="card_account.html",
        context={
            "customer": customer,
            "masked_phone": _mask_phone(customer.phone),
            "weekday_th": weekday_th,
            "nav_inbox_badge": await _inbox_unread_count(db, customer.id),
            "nav_gifts_badge": await _active_gifts_count(db, customer.id),
            "text_size": customer.text_size or "md",
        },
    )


@router.get("/card/account/notifications", response_class=HTMLResponse)
async def card_account_notifications(
    request: Request,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """settings.notif — channel preference + per-shop mute list. Three
    radio options per the May 1 design:

      - "แต้มดี" (default) → preferred_channel=None, waterfall picks
        web_push/line/sms/inbox by what's reachable + cheapest.
      - "LINE" → preferred_channel='line'. Disabled (greyed) until the
        customer links a LINE account; greyed row exposes a
        "ผูกที่นี่" link to /auth/line/customer/start.
      - "เบอร์โทร (SMS)" → preferred_channel='sms'. Same gating against
        customer.phone, link target is /card/save (OTP page).

    The legacy 'inbox' pin value is read as "แต้มดี" for display so
    customers who picked it under the old 2-option UI still see a
    coherent state — they keep their stored preference until they
    explicitly change it.
    """
    from app.models import CustomerShopMute
    customer, was_created = await find_or_create_customer(customer_cookie, db)
    rows = (await db.exec(
        select(CustomerShopMute, Shop)
        .join(Shop, Shop.id == CustomerShopMute.shop_id)
        .where(CustomerShopMute.customer_id == customer.id)
        .order_by(CustomerShopMute.created_at.desc())
    )).all()
    muted = [{"shop": shop} for _mute, shop in rows]

    pref = customer.preferred_channel
    if pref == "line":
        selected_channel = "line"
    elif pref == "sms":
        selected_channel = "sms"
    else:
        selected_channel = "taemdee"

    response = templates.TemplateResponse(
        request=request,
        name="card_account_notifications.html",
        context={
            "customer": customer,
            "notifications_enabled": customer.notifications_enabled,
            "selected_channel": selected_channel,
            "has_line": bool(customer.line_id),
            "has_phone": bool(customer.phone),
            "muted": muted,
            "nav_inbox_badge": await _inbox_unread_count(db, customer.id),
            "nav_gifts_badge": await _active_gifts_count(db, customer.id),
        },
    )
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


@router.post("/card/account/notifications")
async def card_account_notifications_post(
    channel: str = Form("taemdee"),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Save the channel preference. 'taemdee' clears preferred_channel
    (waterfall picks cheapest reachable). 'line' / 'sms' pin to that
    channel — silently rejected if the customer hasn't linked the
    matching identity, since the picker UI gates the option there too."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    if channel == "line" and customer.line_id:
        customer.preferred_channel = "line"
    elif channel == "sms" and customer.phone:
        customer.preferred_channel = "sms"
    else:
        # 'taemdee' (default) or attempt to pick a not-yet-linked
        # provider — fall back to waterfall. The picker disables those
        # options client-side, but a hand-crafted POST still lands here.
        customer.preferred_channel = None
    db.add(customer)
    await db.commit()
    return RedirectResponse(
        url="/card/account/notifications",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/card/account/notifications/master")
async def card_account_notifications_master_toggle(
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Flip the master "รับข้อความจากร้าน" toggle (settings.notif top
    row). When OFF, DeeReach._audience_for excludes this customer from
    every campaign kind across every shop — wider net than the per-
    shop CustomerShopMute. Single POST endpoint, server-driven flip
    (no client toggle state)."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    customer.notifications_enabled = not customer.notifications_enabled
    db.add(customer)
    await db.commit()
    return RedirectResponse(
        url="/card/account/notifications",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/card/account/mute/{shop_id}/mute")
async def card_account_mute(
    shop_id: uuid.UUID,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Mute DeeReach from a specific shop — wired from the inbox row's
    'ปิดเสียงร้านนี้' link so customers can opt out of a chatty shop
    without going through the notifications page. Idempotent: a re-mute
    does nothing. Returns 204 so the JS handler can fade the row in
    place without a navigation."""
    from app.models import CustomerShopMute
    customer, _ = await find_or_create_customer(customer_cookie, db)
    existing = (await db.exec(
        select(CustomerShopMute).where(
            CustomerShopMute.customer_id == customer.id,
            CustomerShopMute.shop_id == shop_id,
        )
    )).first()
    if existing is None:
        db.add(CustomerShopMute(customer_id=customer.id, shop_id=shop_id))
        await db.commit()
    return JSONResponse(status_code=204, content=None)


@router.post("/card/account/mute/{shop_id}/unmute")
async def card_account_unmute(
    shop_id: uuid.UUID,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Remove the customer's mute on a specific shop — re-allows DeeReach
    from that shop. Idempotent: missing row is a 303 anyway."""
    from app.models import CustomerShopMute
    customer, _ = await find_or_create_customer(customer_cookie, db)
    row = (await db.exec(
        select(CustomerShopMute).where(
            CustomerShopMute.customer_id == customer.id,
            CustomerShopMute.shop_id == shop_id,
        )
    )).first()
    if row is not None:
        await db.delete(row)
        await db.commit()
    return RedirectResponse(
        url="/card/account/notifications",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/card/account/text-size")
async def card_account_text_size(
    size: str = Form(...),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Save the C6 'ขนาดตัวอักษร' choice. 'md' (default) clears the column;
    'sm' and 'lg' persist. Returns 204 — caller is the inline JS on
    /card/account which mirrors the choice into localStorage for the next
    page-load's first-paint zoom."""
    if size not in ("sm", "md", "lg"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ขนาดไม่ถูกต้อง")
    customer, _ = await find_or_create_customer(customer_cookie, db)
    customer.text_size = None if size == "md" else size
    db.add(customer)
    await db.commit()
    return JSONResponse(status_code=204, content=None)


@router.post("/card/account/logout")
async def customer_logout():
    """Clear the customer cookie and bounce to home."""
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(CUSTOMER_COOKIE_NAME, path="/")
    return response


@router.post("/link/snooze")
async def link_prompt_snooze(
    request: Request,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Stamp customer.last_link_prompt_snoozed_at = now() so link.prompt
    stays hidden for 14 days. Bounce back to wherever the form was POSTed
    from (Referer) so the customer keeps their place — falls back to
    /my-cards if Referer is missing/cross-origin."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    customer.last_link_prompt_snoozed_at = utcnow()
    db.add(customer)
    await db.commit()
    referer = request.headers.get("referer")
    target = referer if referer and referer.startswith(str(request.base_url)) else "/my-cards"
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/my-id", response_class=HTMLResponse)
async def my_id(
    request: Request,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Customer's identity QR — shop scans this to issue a stamp at the counter
    when the customer doesn't want to scan the printed shop QR themselves
    (forgot which shop they're at, easier to hand the phone over, etc.).

    Renders a fullscreen page with a QR encoding the customer's short URL
    `https://<host>/c/{customer_id}` — the shop's S3.scan modal decodes that
    to extract the customer id and post a stamp.
    """
    customer, was_created = await find_or_create_customer(customer_cookie, db)
    base_url = str(request.base_url).rstrip("/")
    identity_url = f"{base_url}/c/{customer.id}"
    import segno
    qr_svg = segno.make(identity_url, error="m").svg_inline(
        scale=10, dark="#111111", light="#ffffff", border=1, omitsize=True
    )
    response = templates.TemplateResponse(
        request=request,
        name="my_id.html",
        context={
            "customer": customer,
            "qr_svg": qr_svg,
            "identity_url": identity_url,
            "nav_inbox_badge": await _inbox_unread_count(db, customer.id),
            "nav_gifts_badge": await _active_gifts_count(db, customer.id),
        },
    )
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


@router.get("/c/{customer_id}", response_class=HTMLResponse)
async def customer_identity_redirect(customer_id: uuid.UUID):  # noqa: ARG001
    """Bare customer-identity URL. The path param is validated as a UUID by
    FastAPI (rejects garbage with 422) but we don't use it directly — the
    URL is a target for the shop's S3.scan QR decoder. When a human follows
    the link, redirect them to /my-cards (or login if not claimed yet).
    """
    return RedirectResponse(url="/my-cards", status_code=status.HTTP_303_SEE_OTHER)


async def _resolve_branch(
    db: AsyncSession, shop_id: uuid.UUID, branch_param: Optional[uuid.UUID], customer_id: uuid.UUID
) -> Optional[Branch]:
    """Pick the branch to display in the DeeCard wordmark sub.

    Priority: explicit ?branch=<id> query → most-recent active stamp's branch → None.
    The fallback lets the card "remember" the last branch the customer visited.
    """
    if branch_param:
        b = await db.get(Branch, branch_param)
        if b and b.shop_id == shop_id:
            return b
    last_branch_id = (await db.exec(
        select(Point.branch_id)
        .where(
            Point.shop_id == shop_id,
            Point.customer_id == customer_id,
            Point.is_voided == False,  # noqa: E712
            Point.branch_id.is_not(None),
        )
        .order_by(Point.created_at.desc())
        .limit(1)
    )).first()
    if last_branch_id:
        return await db.get(Branch, last_branch_id)
    return None


@router.get("/card/{shop_id}", response_class=HTMLResponse)
async def view_card(
    request: Request,
    shop_id: uuid.UUID,
    branch: Optional[uuid.UUID] = None,
    stamped: int = 0,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    shop = await db.get(Shop, shop_id)
    if not shop:
        # Friendly HTML page rather than a JSON 404 — customer most likely
        # arrived via an old QR or a deleted shop's bookmark.
        return templates.TemplateResponse(
            request=request,
            name="shop_not_found.html",
            context={"shop_id": shop_id},
            status_code=status.HTTP_404_NOT_FOUND,
        )

    customer, was_created = await find_or_create_customer(customer_cookie, db)
    # Anyone with display_name still NULL hasn't completed C2 onboarding —
    # bounce them through the dedicated /onboard flow. /onboard now
    # issues a Point inline if there isn't one yet, so the customer
    # ends up at /my-cards with at least one card after the flow.
    if customer.display_name is None:
        response = RedirectResponse(url=f"/onboard/{shop_id}", status_code=status.HTTP_303_SEE_OTHER)
        if was_created:
            set_customer_cookie(response, customer.id)
        return response
    point_count = await active_point_count(db, shop.id, customer.id)
    branch_obj = await _resolve_branch(db, shop.id, branch, customer.id)

    # First-visit detection: no redemptions yet AND only 1 active stamp.
    # Surfaces the C2 welcome banner + "save my stamps" CTA above the stamp grid.
    is_first_visit = False
    if point_count == 1:
        prior_redemptions = (await db.exec(
            select(func.count())
            .select_from(Redemption)
            .where(Redemption.shop_id == shop.id, Redemption.customer_id == customer.id)
        )).one()
        is_first_visit = prior_redemptions == 0

    # link.prompt — soft sheet shown on shop.daily once a still-anon
    # customer has collected ≥3 active stamps total (across all shops)
    # and either never snoozed or snoozed ≥14 days ago. Don't show on
    # the celebration screen (just_stamped) — that moment is reserved
    # for the new-stamp confetti, not a conversion ask.
    show_link_prompt = False
    if customer.is_anonymous and not stamped:
        total_active_stamps = (await db.exec(
            select(func.count())
            .select_from(Point)
            .where(
                Point.customer_id == customer.id,
                Point.is_voided == False,  # noqa: E712
                Point.redemption_id.is_(None),
            )
        )).one()
        if total_active_stamps >= 3:
            snoozed = customer.last_link_prompt_snoozed_at
            if snoozed is None or (utcnow() - snoozed).days >= 14:
                show_link_prompt = True

    response = templates.TemplateResponse(
        request=request,
        name="themes/default.html",
        context={
            "shop": shop,
            "point_count": point_count,
            "customer": customer,
            "is_first_visit": is_first_visit,
            "branch": branch_obj,
            # `?stamped=1` from the scan redirect — triggers the celebration
            # overlay once. Fires on first visits too: the C2 banner is a
            # contextual "this is your first one here" note, not a substitute
            # for the confetti moment every stamp deserves.
            "just_stamped": bool(stamped),
            "show_link_prompt": show_link_prompt,
            "nav_inbox_badge": await _inbox_unread_count(db, customer.id),
            "nav_gifts_badge": await _active_gifts_count(db, customer.id),
        },
    )
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


@router.get("/scan-shop", response_class=HTMLResponse)
async def scan_shop(request: Request):
    """C7 camera viewfinder — opens the device camera, decodes shop QR codes,
    and navigates to `/scan/{shop_id}` on a valid hit. Linked from the C7
    'สแกน QR ของร้าน' button in /my-cards."""
    return templates.TemplateResponse(
        request=request,
        name="scan_shop.html",
        context={},
    )


@router.get("/onboard/{shop_id}", response_class=HTMLResponse)
async def onboard(
    request: Request,
    shop_id: uuid.UUID,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """C2 onboarding — 3-step Alpine flow shown on the customer's first
    encounter with a shop (display_name IS NULL). C2.2 needs to show
    a non-zero stamp count, otherwise the "ผมเก็บแต้มแรกให้แล้ว" copy
    rings false and customers land at /my-cards with no cards after
    completing the flow. Issue a stamp inline if the customer arrived
    here via /card (no Point yet) — same path /scan would have taken
    if they'd hit the QR. Cooldown is silently swallowed: a returner
    with prior stamps has nothing to issue, the existing count carries
    onboarding through."""
    shop = await db.get(Shop, shop_id)
    if not shop:
        return RedirectResponse(url=f"/card/{shop_id}", status_code=status.HTTP_303_SEE_OTHER)
    customer, was_created = await find_or_create_customer(customer_cookie, db)
    point_count = await active_point_count(db, shop.id, customer.id)
    if point_count == 0:
        try:
            stamp, _ = await issue_point(db, shop, customer, method="customer_scan")
            publish(
                shop.id,
                "feed-row",
                feed_row_html("point", stamp.id, bkk_feed_time(stamp.created_at), customer.display_name or "ลูกค้า"),
            )
            point_count = 1
        except IssuanceError:
            # Cooldown / branch-required / etc. — leave point_count at 0
            # and let the template render the 0-stamp fallback rather
            # than 500.
            pass
    response = templates.TemplateResponse(
        request=request,
        name="onboard.html",
        context={
            "shop": shop,
            "customer": customer,
            "point_count": point_count,
        },
    )
    # Re-issue the cookie if the customer was created here — covers the case
    # where iOS Safari dropped the Set-Cookie from /scan's 303 redirect, which
    # would otherwise spawn a fresh anonymous customer per page load and leave
    # the saved nickname on a phantom row that the next request can't find.
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


@router.get("/onboard/{shop_id}/recovery", response_class=HTMLResponse)
async def onboard_recovery(
    request: Request,
    shop_id: uuid.UUID,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """C2.4 — recovery code shown after a customer skips signup at C2.3. The
    code is generated on demand (and cached on the row) so the same customer
    sees the same code if they revisit. Continue button → /card/{shop_id}."""
    shop = await db.get(Shop, shop_id)
    if not shop:
        return RedirectResponse(url=f"/card/{shop_id}", status_code=status.HTTP_303_SEE_OTHER)
    customer, was_created = await find_or_create_customer(customer_cookie, db)
    code = await ensure_recovery_code(db, customer)
    response = templates.TemplateResponse(
        request=request,
        name="onboard_recovery.html",
        context={"shop": shop, "customer": customer, "recovery_code": code},
    )
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


@router.get("/story/{shop_id}", response_class=HTMLResponse)
async def shop_story(
    request: Request,
    shop_id: uuid.UUID,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """shop.story — Robinhood-style page showing the shop the customer
    is collecting points at. Cover hero (gradient + tagline) overlapped
    by a shop card; below it a points strip (when they have stamps),
    the owner's story, the contact info. Menu items deferred until the
    shop_menu_items model exists."""
    shop = await db.get(Shop, shop_id)
    if not shop:
        return templates.TemplateResponse(
            request=request,
            name="shop_not_found.html",
            context={"shop_id": shop_id},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    customer, was_created = await find_or_create_customer(customer_cookie, db)

    # Cover eyebrow — Buddhist year derived from shop.created_at. Reads
    # naturally for Thai customers ("ตั้งแต่ปี 2562") even if the shop
    # itself only started using TaemDee yesterday; close enough for the
    # tagline strip without giving the shop owner a separate "founded
    # year" form to fill out.
    from datetime import datetime, timezone
    cover_eyebrow = f"ตั้งแต่ปี {shop.created_at.year + 543}"

    # Cover headline — short personal note from S10 onboarding, falls
    # back to the shop name so the gradient hero doesn't look empty.
    cover_headline = shop.thanks_message or shop.name

    # Points strip — only render when there's something to show.
    point_count = await active_point_count(db, shop.id, customer.id)
    points_block = None
    if point_count > 0:
        threshold = shop.reward_threshold or 1
        ratio = min(point_count / threshold, 1.0)
        remaining = max(threshold - point_count, 0)
        points_block = {
            "count": point_count,
            "threshold": threshold,
            "remaining": remaining,
            "ratio_pct": int(ratio * 100),
            "ready": point_count >= threshold,
        }

    # Open-now badge from opening_hours JSON. Tolerant of missing data —
    # any parse hiccup leaves open_status=None and the meta line shows
    # the address only.
    bkk = datetime.now(timezone.utc).astimezone(BKK)
    day_key = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[bkk.weekday()]
    open_status = None
    if shop.opening_hours:
        today = shop.opening_hours.get(day_key) or {}
        if today and not today.get("closed"):
            try:
                open_h, open_m = map(int, today.get("open", "00:00").split(":"))
                close_h, close_m = map(int, today.get("close", "00:00").split(":"))
                now_minutes = bkk.hour * 60 + bkk.minute
                open_minutes = open_h * 60 + open_m
                close_minutes = close_h * 60 + close_m
                if open_minutes <= now_minutes < close_minutes:
                    open_status = {"open": True, "until": today.get("close")}
                else:
                    open_status = {"open": False, "next": today.get("open")}
            except (ValueError, AttributeError):
                pass
        elif today:
            open_status = {"open": False, "next": None}

    # Address line for the shop card sub.
    address_parts = [p for p in (shop.district, shop.location) if p]
    shop_address = " · ".join(address_parts) if address_parts else None

    # เมนูเด็ด — surface up to 6 items, sort_order asc. Owner manages
    # the list at /shop/settings/menu.
    from app.models import ShopMenuItem
    menu_items = (await db.exec(
        select(ShopMenuItem)
        .where(ShopMenuItem.shop_id == shop.id)
        .order_by(ShopMenuItem.sort_order, ShopMenuItem.created_at)
        .limit(6)
    )).all()

    response = templates.TemplateResponse(
        request=request,
        name="c9_story.html",
        context={
            "shop": shop,
            "customer": customer,
            "cover_eyebrow": cover_eyebrow,
            "cover_headline": cover_headline,
            "points_block": points_block,
            "open_status": open_status,
            "shop_address": shop_address,
            "menu_items": list(menu_items),
            "nav_inbox_badge": await _inbox_unread_count(db, customer.id),
            "nav_gifts_badge": await _active_gifts_count(db, customer.id),
        },
    )
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


@router.get("/recover", response_class=HTMLResponse)
async def recover_form(request: Request):
    """Standalone recovery entry page — paste a recovery code, get the
    customer cookie swapped to that account. Linked from /card/save and the
    onboarding skip path."""
    return templates.TemplateResponse(
        request=request, name="recover.html", context={"error": None}
    )


@router.post("/recover", response_class=HTMLResponse)
async def recover_submit(
    request: Request,
    code: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    customer = await find_by_code(db, code)
    if customer is None:
        return templates.TemplateResponse(
            request=request,
            name="recover.html",
            context={"error": "ไม่พบรหัสนี้ — ลองตรวจสอบอีกครั้งนะครับ"},
            status_code=400,
        )
    response = RedirectResponse(url="/my-cards", status_code=status.HTTP_303_SEE_OTHER)
    set_customer_cookie(response, customer.id)
    return response


@router.get("/scan/{shop_id}")
async def scan(
    request: Request,
    shop_id: uuid.UUID,
    branch: Optional[uuid.UUID] = None,
    t: Optional[str] = None,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Target of the shop's printed QR. Issues a stamp (if eligible), redirects to card view.

    Branch-specific QRs encode `?branch=<id>` so the issued stamp is tagged to that
    branch and the customer's DeeCard shows the branch name in the wordmark sub.

    `?t=<jwt>` carries the live-QR (S3.qr) freshness token. When present
    the JWT must validate (not expired, signed for this shop) — screenshots
    of stale on-screen QRs hit this branch and get a friendly 410. Bare
    /scan/{shop_id} (no token) keeps working for printed sticker QRs.
    """
    if t:
        from app.services.auth import verify_live_qr_token
        if not verify_live_qr_token(t, shop_id):
            return templates.TemplateResponse(
                request=request,
                name="scan_expired.html",
                context={"shop_id": shop_id},
                status_code=status.HTTP_410_GONE,
            )

    shop = await db.get(Shop, shop_id)
    if not shop:
        # Forward to /card/{shop_id} so the friendly "ไม่พบร้านนี้" page renders.
        return RedirectResponse(url=f"/card/{shop_id}", status_code=status.HTTP_303_SEE_OTHER)

    branch_obj: Optional[Branch] = None
    if branch:
        branch_obj = await db.get(Branch, branch)
        if not branch_obj or branch_obj.shop_id != shop.id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "ไม่พบสาขานี้ในร้าน — QR ที่สแกนอาจเป็นของสาขาที่ปิดไปแล้ว",
            )

    customer, was_created = await find_or_create_customer(customer_cookie, db)

    just_stamped = False
    auto_redemption = None
    try:
        stamp, auto_redemption = await issue_point(
            db, shop, customer,
            method="customer_scan",
            branch_id=branch_obj.id if branch_obj else None,
        )
        publish(
            shop.id,
            "feed-row",
            feed_row_html("point", stamp.id, bkk_feed_time(stamp.created_at), customer.display_name or "ลูกค้า"),
        )
        just_stamped = True
    except IssuanceError:
        # Cooldown or other constraint — silently swallow; redirect lands on card.
        pass

    # First-ever stamp lands the customer in the C2 onboarding flow
    # (3-step welcome + reward preview + signup). Gate on just_stamped:
    # a cooldown'd re-scan from a customer who hasn't named themselves
    # yet shouldn't reset them through onboarding when they likely
    # already have stamps to view. Returners with display_name set
    # ("คุณลูกค้า" included) skip onboarding regardless.
    if just_stamped and customer.display_name is None:
        redirect_url = f"/onboard/{shop_id}"
        if branch_obj:
            redirect_url += f"?branch={branch_obj.id}"
        response = RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
        if was_created:
            set_customer_cookie(response, customer.id)
        return response

    # Auto-redeem fires inside issue_point() now — single source of truth
    # across every issuance entry point. If this scan tripped the threshold,
    # `auto_redemption` is the freshly-created Redemption row; we just
    # publish the dashboard + customer SSE events and skip C4 to land on
    # the celebration page directly.
    if auto_redemption is not None:
        publish(
            shop.id,
            "feed-row",
            feed_row_html("redemption", auto_redemption.id, bkk_feed_time(auto_redemption.created_at), customer.display_name or "ลูกค้า"),
        )
        from app.services.events import publish_customer
        import asyncio
        loop = asyncio.get_event_loop()
        loop.create_task(publish_gifts_update(customer.id))
        response = RedirectResponse(
            url=f"/card/{shop_id}/claimed?r={auto_redemption.id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        if was_created:
            set_customer_cookie(response, customer.id)
        return response

    params = []
    if branch_obj:
        params.append(f"branch={branch_obj.id}")
    if just_stamped:
        # Triggers the celebration overlay on the card view (one-shot, fades out).
        params.append("stamped=1")
    redirect_url = f"/card/{shop_id}" + ("?" + "&".join(params) if params else "")
    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


@router.post("/card/{shop_id}/redeem")
async def redeem_reward(
    shop_id: uuid.UUID,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "ไม่พบร้านค้านี้ — อาจถูกลบไปแล้ว ไม่สามารถรับรางวัลได้",
        )

    customer, _ = await find_or_create_customer(customer_cookie, db)

    # Per the May 1 design: guests can redeem without forced signup. The
    # voucher is bound to the customer cookie; the C4 page surfaces a soft
    # "สมัครเพื่อเก็บบัตรไม่ให้หาย" link as an optional save-card pitch.
    try:
        redemption = await redeem(db, shop, customer)
    except RedemptionError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    publish(
        shop.id,
        "feed-row",
        feed_row_html("redemption", redemption.id, bkk_feed_time(redemption.created_at), customer.display_name or "ลูกค้า"),
    )
    # Bump the customer's `ของขวัญ` dock badge in real time (other open
    # tabs / the just-redirected /claimed page get the new count without
    # waiting for the next page navigation).
    from app.services.events import publish_customer
    publish_customer(customer.id, "gifts-update", str(await _active_gifts_count(db, customer.id)))

    return RedirectResponse(
        url=f"/card/{shop_id}/claimed?r={redemption.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/card/{shop_id}/claimed", response_class=HTMLResponse)
async def reward_claimed(
    request: Request,
    shop_id: uuid.UUID,
    r: uuid.UUID,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """C5 — celebration screen shown right after a redemption."""
    shop = await db.get(Shop, shop_id)
    if not shop:
        # Customer landed on /claimed for a deleted shop — friendly page wins.
        return templates.TemplateResponse(
            request=request,
            name="shop_not_found.html",
            context={"shop_id": shop_id},
            status_code=status.HTTP_404_NOT_FOUND,
        )

    redemption = await db.get(Redemption, r)
    if not redemption or redemption.shop_id != shop.id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "ไม่พบรายการรับรางวัลนี้ — อาจถูกยกเลิกหรือลิงก์เก่าไปแล้ว",
        )

    customer, _ = await find_or_create_customer(customer_cookie, db)
    return templates.TemplateResponse(
        request=request,
        name="card_claimed.html",
        context={
            "shop": shop,
            "redemption": redemption,
            "nav_inbox_badge": await _inbox_unread_count(db, customer.id),
            "nav_gifts_badge": await _active_gifts_count(db, customer.id),
        },
    )


@router.post("/card/claim/phone")
async def claim_phone(
    phone: str = Form(...),
    code: str = Form(...),
    display_name: Optional[str] = Form(None),
    next_redeem: Optional[str] = Form(None),
    dr_consent: Optional[str] = Form("on"),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Soft Wall: customer verifies their phone via OTP and merges/promotes the
    anonymous account to a claimed one. Refreshes the customer cookie since the
    resulting customer_id may change (merge case).

    `dr_consent` reflects the C3 toggle's state at submit time:
      'on'  → opt in to DeeReach from the shop they're signing up at
              (default — no mute row written).
      'off' → mute that shop so future DeeReach campaigns skip them.
              The mute is per-shop (PRD §10), other shops they collect
              stamps at later are unaffected.

    If `next_redeem=<shop_id>` is set (the C4 gate flow passes it through the
    sheet), we attempt the redemption immediately and return its claimed URL
    in `next_url` so the frontend lands on C5 directly. Best-effort —
    failures fall back to `next_url=/my-cards`.
    """
    if not await verify_otp(db, phone, code):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "รหัส OTP ไม่ถูกต้องหรือหมดอายุแล้ว — กรุณากดส่งใหม่",
        )

    customer, _ = await find_or_create_customer(customer_cookie, db)
    claimed = await claim_by_phone(db, customer, phone, display_name=display_name)

    next_url = "/my-cards"
    target_shop_id: Optional[uuid.UUID] = None
    if next_redeem:
        try:
            target_shop_id = uuid.UUID(next_redeem)
        except ValueError:
            target_shop_id = None
    if target_shop_id:
        shop = await db.get(Shop, target_shop_id)
        if shop:
            try:
                redemption = await redeem(db, shop, claimed)
                publish(
                    shop.id,
                    "feed-row",
                    feed_row_html("redemption", redemption.id, bkk_feed_time(redemption.created_at), claimed.display_name or "ลูกค้า"),
                )
                next_url = f"/card/{shop.id}/claimed?r={redemption.id}"
            except RedemptionError:
                pass  # fall through to /my-cards

    # Honour the consent toggle — opt-out → write a CustomerShopMute row
    # for the shop the customer just claimed at. _audience_for filters on
    # this in the DeeReach pipeline.
    if dr_consent != "on" and target_shop_id:
        from app.models import CustomerShopMute
        existing_mute = (await db.exec(
            select(CustomerShopMute).where(
                CustomerShopMute.customer_id == claimed.id,
                CustomerShopMute.shop_id == target_shop_id,
            )
        )).first()
        if not existing_mute:
            db.add(CustomerShopMute(customer_id=claimed.id, shop_id=target_shop_id))
            await db.commit()

    response = JSONResponse({"claimed": True, "customer_id": str(claimed.id), "next_url": next_url})
    set_customer_cookie(response, claimed.id)
    return response


SKIP_NICKNAME_DEFAULT = "คุณลูกค้า"


@router.post("/card/nickname")
async def save_nickname(
    name: str = Form(""),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """C2.welcome — save the display name a guest provided in the welcome
    sheet. Empty string = skip; we still set a polite default ("คุณลูกค้า")
    so the welcome sheet sees a non-null display_name on the next page load
    and doesn't reopen. NULL on customer.display_name is the "ask me" signal.
    """
    customer, was_created = await find_or_create_customer(customer_cookie, db)
    cleaned = (name or "").strip()
    customer.display_name = cleaned if cleaned else SKIP_NICKNAME_DEFAULT
    db.add(customer)
    await db.commit()
    response = JSONResponse({"ok": True, "display_name": customer.display_name})
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


@router.post("/card/account/delete")
async def delete_my_account(
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """PDPA: scrub the current customer's identity; stamps stay (anonymized)."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    await delete_customer_account(db, customer)
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(CUSTOMER_COOKIE_NAME, path="/")
    return response


@router.get("/my-cards", response_class=HTMLResponse)
async def my_cards(
    request: Request,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """C7 — list of every shop the customer has points at. Per the revised
    design, guests can see their cards too (cookie-bound on this device);
    the page just adds the green signup banner + picker so they can convert.
    """
    customer, was_created = await find_or_create_customer(customer_cookie, db)

    # Active points grouped by shop. Same active-point rule as redemption service.
    points_per_shop = (await db.exec(
        select(Point.shop_id, func.count().label("active_count"))
        .where(
            Point.customer_id == customer.id,
            Point.is_voided == False,  # noqa: E712
            Point.redemption_id.is_(None),
        )
        .group_by(Point.shop_id)
    )).all()

    # Per-shop unread inbox count → small accent dot on the c7-card so the
    # customer sees which shop has a pending message without opening /my-inbox.
    unread_per_shop = dict((await db.exec(
        select(Inbox.shop_id, func.count())
        .where(Inbox.customer_id == customer.id, Inbox.read_at.is_(None))
        .group_by(Inbox.shop_id)
    )).all())

    cards = []
    for shop_id, active_count in points_per_shop:
        shop = await db.get(Shop, shop_id)
        if shop is None:
            continue
        threshold = shop.reward_threshold or 1
        ratio = active_count / threshold
        cards.append({
            "shop": shop,
            "point_count": active_count,
            "threshold": threshold,
            "remaining": max(threshold - active_count, 0),
            "ratio": ratio,
            "ratio_pct": min(int(ratio * 100), 100),
            "unread": unread_per_shop.get(shop_id, 0),
            "shop_color": _shop_swatch(shop_id),
        })
    cards.sort(key=lambda c: c["ratio"], reverse=True)

    # Hero now points at all unused vouchers (Redemption served_at IS NULL ·
    # is_voided = False) as a carousel instead of a single hero tile.
    # Auto-redeem on /scan means the threshold-hit moment immediately
    # collapses into a Redemption row — by the time the customer lands on
    # /my-cards there's a voucher waiting in /my-gifts, not a full unredeemed
    # card. The hero surfaces "ของขวัญรอพี่อยู่" with a tap-through to use it.
    hero_redemptions = (await db.exec(
        select(Redemption)
        .where(
            Redemption.customer_id == customer.id,
            Redemption.served_at.is_(None),
            Redemption.is_voided == False,  # noqa: E712
        )
        .order_by(Redemption.created_at.desc())
    )).all()
    hero_vouchers = []
    for redemption in hero_redemptions:
        shop = await db.get(Shop, redemption.shop_id)
        if shop is not None:
            hero_vouchers.append({
                "redemption": redemption,
                "shop": shop,
            })

    # near + other zones still come from the points view since they're about
    # accumulation progress, not active vouchers.
    near_cards = [c for c in cards if c["ratio"] >= 0.5]
    other_cards = [c for c in cards if c["ratio"] < 0.5]

    total_stamps = sum(c["point_count"] for c in cards)

    from app.models.util import BKK
    from datetime import datetime, timezone
    weekday_th = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")[
        datetime.now(timezone.utc).astimezone(BKK).weekday()
    ]

    # Per PRD §10 / DeeReach inbox channel — surface unread DeeReach
    # messages so the customer notices and reads them.
    inbox_unread = (await db.exec(
        select(func.count())
        .select_from(Inbox)
        .where(Inbox.customer_id == customer.id, Inbox.read_at.is_(None))
    )).one()

    response = templates.TemplateResponse(
        request=request,
        name="my_cards.html",
        context={
            "customer": customer,
            "cards": cards,
            "hero_vouchers": hero_vouchers,
            "near_cards": near_cards,
            "other_cards": other_cards,
            "total_stamps": total_stamps,
            "weekday_th": weekday_th,
            "inbox_unread": inbox_unread,
            "nav_gifts_badge": await _active_gifts_count(db, customer.id),
        },
    )
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


# ── Customer inbox (DeeReach channel "inbox" lands here) ─────────────────────

_REWARD_EMOJI = {
    "coffee_cup": "☕",
    "latte_art": "☕",
    "iced": "🧋",
    "card": "🎟️",
    "gift_box": "🎁",
    "star": "✨",
}
_GIFT_ICON_PALETTE = ("butter", "mint", "accent")


def _gift_emoji(reward_image: Optional[str]) -> str:
    return _REWARD_EMOJI.get(reward_image or "", "🎁")


def _bkk_short_date(dt) -> str:
    """Render a Redemption.served_at / created_at as Thai short date
    (e.g., '14 ก.พ.') for the gifts list. Naive UTC → Asia/Bangkok."""
    from datetime import timezone
    from app.models.util import BKK, _THAI_MONTH_SHORT
    bkk = dt.replace(tzinfo=timezone.utc).astimezone(BKK)
    return f"{bkk.day} {_THAI_MONTH_SHORT[bkk.month - 1]}"


@router.get("/my-gifts", response_class=HTMLResponse)
async def my_gifts(
    request: Request,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """C-gifts — list of vouchers (พร้อมใช้ + ใช้แล้ว). Surfaces the
    customer's Redemption rows: served_at IS NULL = active voucher
    waiting to be claimed at the shop, served_at set = used.
    Active gifts link to /card/{shop}/claimed?r={id} so the customer
    can show the voucher to staff. Used gifts are read-only."""
    customer, was_created = await find_or_create_customer(customer_cookie, db)

    active_rows = (await db.exec(
        select(Redemption, Shop)
        .join(Shop, Shop.id == Redemption.shop_id)
        .where(
            Redemption.customer_id == customer.id,
            Redemption.is_voided == False,  # noqa: E712
            Redemption.served_at.is_(None),
        )
        .order_by(Redemption.created_at.desc())
    )).all()

    used_rows = (await db.exec(
        select(Redemption, Shop)
        .join(Shop, Shop.id == Redemption.shop_id)
        .where(
            Redemption.customer_id == customer.id,
            Redemption.served_at.is_not(None),
        )
        .order_by(Redemption.served_at.desc())
        .limit(30)
    )).all()

    active_gifts = [
        {
            "id": r.id,
            "name": s.reward_description,
            "shop_name": s.name,
            "emoji": _gift_emoji(s.reward_image),
            "icon_color": _GIFT_ICON_PALETTE[i % len(_GIFT_ICON_PALETTE)],
            "use_url": f"/card/{s.id}/claimed?r={r.id}",
        }
        for i, (r, s) in enumerate(active_rows)
    ]
    used_gifts = [
        {
            "id": r.id,
            "name": s.reward_description,
            "shop_name": s.name,
            "emoji": _gift_emoji(s.reward_image),
            "used_at": _bkk_short_date(r.served_at) if r.served_at else None,
        }
        for r, s in used_rows
    ]

    response = templates.TemplateResponse(
        request=request,
        name="my_gifts.html",
        context={
            "customer": customer,
            "active_gifts": active_gifts,
            "used_gifts": used_gifts,
            "nav_inbox_badge": await _inbox_unread_count(db, customer.id),
            "nav_gifts_badge": len(active_gifts),
        },
    )
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


@router.post("/voucher/{redemption_id}/use")
async def voucher_mark_used(
    redemption_id: uuid.UUID,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """voucher.use activation — trust-based: tapping "ใช้" in gifts.list
    stamps the redemption's served_at right now, no shop confirmation.
    The follow-up GET /voucher/<id> renders the fullscreen QR the
    customer shows to staff (audit-trail only). Idempotent — second
    POST is a no-op since served_at is already set."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    redemption = await db.get(Redemption, redemption_id)
    if not redemption or redemption.customer_id != customer.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบของขวัญนี้")
    if redemption.is_voided:
        raise HTTPException(status.HTTP_410_GONE, "ของขวัญถูกยกเลิกไปแล้ว")
    if redemption.served_at is None:
        redemption.served_at = utcnow()
        db.add(redemption)
        await db.commit()
        # Notify the shop dashboard so the activity feed shows this
        # voucher being consumed in real time, mirroring the
        # /shop/issue/scan publish path.
        publish(
            redemption.shop_id,
            "feed-row",
            feed_row_html(
                "redemption",
                redemption.id,
                bkk_feed_time(redemption.served_at),
                customer.display_name or "ลูกค้า",
            ),
        )
        # Decrement the customer's `ของขวัญ` dock badge — this voucher
        # just moved from "พร้อมใช้" to "ใช้แล้ว", so any other open tab
        # (e.g. /my-cards) should reflect the lower count immediately.
        from app.services.events import publish_customer
        publish_customer(customer.id, "gifts-update", str(await _active_gifts_count(db, customer.id)))
    return RedirectResponse(
        url=f"/voucher/{redemption_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/voucher/{redemption_id}", response_class=HTMLResponse)
async def voucher_view(
    request: Request,
    redemption_id: uuid.UUID,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """voucher.use fullscreen — shop sees this on the customer's screen
    when they walk up to claim a saved voucher. QR encodes the
    redemption URL itself so staff with S3.scan can pull up an audit
    record (not yet wired — scanner falls back to its existing
    'unknown QR' flow gracefully)."""
    import segno

    customer, was_created = await find_or_create_customer(customer_cookie, db)
    redemption = await db.get(Redemption, redemption_id)
    if not redemption or redemption.customer_id != customer.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบของขวัญนี้")
    shop = await db.get(Shop, redemption.shop_id)
    base_url = str(request.base_url).rstrip("/")
    qr_url = f"{base_url}/voucher/{redemption_id}"
    qr_svg = segno.make(qr_url, error="m").svg_inline(
        scale=8, border=0, dark="#111"
    )
    response = templates.TemplateResponse(
        request=request,
        name="voucher_use.html",
        context={
            "shop": shop,
            "redemption": redemption,
            "qr_svg": qr_svg,
            # Used-at is the deadline anchor for the 5-minute show-it-
            # to-staff countdown the design specifies. Past that, the
            # template just renders without the timer.
            "used_at": redemption.served_at,
        },
    )
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


@router.get("/my-inbox", response_class=HTMLResponse)
async def my_inbox(
    request: Request,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """List of DeeReach messages this customer has received via the inbox
    channel. Each row carries the shop's name + body; tap to open it
    (POST mark-read endpoint flips read_at)."""
    customer, was_created = await find_or_create_customer(customer_cookie, db)
    rows = (await db.exec(
        select(Inbox).where(Inbox.customer_id == customer.id)
        .order_by(Inbox.created_at.desc())
        .limit(50)
    )).all()

    shop_ids = {r.shop_id for r in rows}
    shops_by_id = {}
    if shop_ids:
        shop_rows = (await db.exec(select(Shop).where(Shop.id.in_(shop_ids)))).all()
        shops_by_id = {s.id: s for s in shop_rows}

    # Existing mutes — used to hide the per-row "ปิดเสียง" link for shops
    # the customer has already opted out of (the link would be a no-op).
    from app.models import CustomerShopMute
    muted_shop_ids = set((await db.exec(
        select(CustomerShopMute.shop_id).where(CustomerShopMute.customer_id == customer.id)
    )).all())

    items = [
        {
            "row": r,
            "shop": shops_by_id.get(r.shop_id),
            "muted": r.shop_id in muted_shop_ids,
        }
        for r in rows
    ]

    # Greeting context for the page-head ("สวัสดีครับพี่X · วันศุกร์ ขอให้
    # เป็นวันที่ดี"). Same shape as /my-cards for consistency.
    from app.models.util import BKK
    from datetime import datetime, timezone
    weekday_th = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")[
        datetime.now(timezone.utc).astimezone(BKK).weekday()
    ]

    unread_count = sum(1 for it in items if it["row"].read_at is None)

    response = templates.TemplateResponse(
        request=request,
        name="my_inbox.html",
        context={
            "customer": customer,
            "items": items,
            "weekday_th": weekday_th,
            "unread_count": unread_count,
            "nav_inbox_badge": unread_count,
            "nav_gifts_badge": await _active_gifts_count(db, customer.id),
        },
    )
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


@router.post("/my-inbox/{inbox_id}/read")
async def my_inbox_mark_read(
    inbox_id: uuid.UUID,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Flip read_at — owner-bound (only the customer who owns the row can
    mark it). Idempotent: re-marking is fine, returns the same 204."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    row = await db.get(Inbox, inbox_id)
    if row is None or row.customer_id != customer.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบข้อความ")
    if row.read_at is None:
        row.read_at = utcnow()
        db.add(row)
        await db.commit()
    return JSONResponse({"ok": True}, status_code=200)


@router.get("/my-inbox/{inbox_id}", response_class=HTMLResponse)
async def my_inbox_detail(
    request: Request,
    inbox_id: uuid.UUID,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Inbox.detail — single message view with shop hero + body + 'open
    card' / 'mute shop' actions. Auto-marks the row as read on view.
    404s if the row belongs to another customer (ownership check) so
    sharing a URL leaks nothing."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    row = await db.get(Inbox, inbox_id)
    if row is None or row.customer_id != customer.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบข้อความ")
    shop = await db.get(Shop, row.shop_id) if row.shop_id else None
    if row.read_at is None:
        row.read_at = utcnow()
        db.add(row)
        await db.commit()

    from app.models import CustomerShopMute
    is_muted = False
    if shop is not None:
        is_muted = (await db.exec(
            select(CustomerShopMute).where(
                CustomerShopMute.customer_id == customer.id,
                CustomerShopMute.shop_id == shop.id,
            )
        )).first() is not None

    return templates.TemplateResponse(
        request=request,
        name="my_inbox_detail.html",
        context={
            "customer": customer,
            "shop": shop,
            "row": row,
            "is_muted": is_muted,
            # Note: this row was just flipped to read above, so the count
            # already excludes it. Badge reflects the next unread, if any.
            "nav_inbox_badge": await _inbox_unread_count(db, customer.id),
            "nav_gifts_badge": await _active_gifts_count(db, customer.id),
        },
    )


# ── Web Push subscription (VAPID) ────────────────────────────────────────────

@router.get("/push/vapid-public")
async def push_vapid_public(db: AsyncSession = Depends(get_session)):
    """Frontend service worker pulls the VAPID public key from here at
    subscribe time. 503 until the worker has generated + persisted the
    keypair on its first boot — UI hides the 'enable notifications'
    button on the same signal."""
    from app.services.web_push import get_vapid_public_key, load_vapid_keys
    await load_vapid_keys(db)
    pub = get_vapid_public_key()
    if not pub:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Web Push not configured")
    return JSONResponse({"public_key": pub})


@router.get("/push/status")
async def push_status(
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Diagnostic — frontend uses this to keep the toggle button honest.
    Returns whether VAPID is configured on the server, the (truncated)
    endpoint we have stored for this customer, and the prefix the browser
    can compare against its own pushManager subscription. If the browser
    reports a subscription but `endpoint_prefix` doesn't match (e.g.
    customer cleared site data and re-subscribed), the JS re-uploads."""
    from app.services.web_push import get_vapid_public_key, load_vapid_keys
    await load_vapid_keys(db)
    customer, _ = await find_or_create_customer(customer_cookie, db)
    return JSONResponse({
        "vapid_configured": bool(get_vapid_public_key()),
        "has_endpoint": bool(customer.web_push_endpoint),
        # Send the first 60 chars only — enough for the JS to spot a
        # mismatch without leaking the full endpoint URL in client logs.
        "endpoint_prefix": (customer.web_push_endpoint or "")[:60],
    })


@router.post("/push/subscribe")
async def push_subscribe(
    endpoint: str = Form(...),
    p256dh: str = Form(...),
    auth: str = Form(...),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Save a Web Push subscription on the current customer. Replacing an
    existing subscription is fine — most browsers rotate endpoints when
    the user clears storage or reinstalls. Idempotent."""
    customer, was_created = await find_or_create_customer(customer_cookie, db)
    customer.web_push_endpoint = endpoint
    customer.web_push_p256dh = p256dh
    customer.web_push_auth = auth
    db.add(customer)
    await db.commit()
    response = JSONResponse({"ok": True}, status_code=200)
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


@router.post("/push/unsubscribe")
async def push_unsubscribe(
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Clear push subscription — used when the customer revokes notification
    permission in their browser. Without this, send_web_push will keep
    hitting an endpoint that returns 410 Gone."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    customer.web_push_endpoint = None
    customer.web_push_p256dh = None
    customer.web_push_auth = None
    db.add(customer)
    await db.commit()
    return JSONResponse({"ok": True}, status_code=200)


