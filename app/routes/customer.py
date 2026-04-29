"""Customer-facing routes: scan to get a stamp, view DeeCard, Soft Wall claim."""

import uuid
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
from app.models.util import bkk_feed_time, utcnow
from app.services.auth import verify_otp
from app.services.events import feed_row_html, publish
from app.services.issuance import IssuanceError, issue_point
from app.services.pdpa import delete_customer_account
from app.services.recovery import ensure_recovery_code, find_by_code
from app.services.redemption import RedemptionError, active_point_count, redeem
from app.services.soft_wall import claim_by_phone

router = APIRouter()


async def _inbox_unread_count(db: AsyncSession, customer_id: uuid.UUID) -> int:
    """Total unread Inbox rows for this customer — used to drive the
    `ข้อความ` tab badge on the customer dock. Cheap single-row count."""
    return (await db.exec(
        select(func.count())
        .select_from(Inbox)
        .where(Inbox.customer_id == customer_id, Inbox.read_at.is_(None))
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


@router.get("/card/account", response_class=HTMLResponse)
async def account_menu(
    request: Request,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """C6 — customer account menu (profile, my-stuff, settings, logout, delete)."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    if customer.is_anonymous:
        return RedirectResponse(url="/card/save", status_code=status.HTTP_303_SEE_OTHER)

    cards_count = (await db.exec(
        select(func.count(func.distinct(Point.shop_id)))
        .where(
            Point.customer_id == customer.id,
            Point.is_voided == False,  # noqa: E712
            Point.redemption_id.is_(None),
        )
    )).one()

    ready_count = 0
    points_per_shop = (await db.exec(
        select(Point.shop_id, func.count().label("active_count"))
        .where(
            Point.customer_id == customer.id,
            Point.is_voided == False,  # noqa: E712
            Point.redemption_id.is_(None),
        )
        .group_by(Point.shop_id)
    )).all()
    for shop_id, active_count in points_per_shop:
        shop = await db.get(Shop, shop_id)
        if shop and active_count >= shop.reward_threshold:
            ready_count += 1

    redemption_count = (await db.exec(
        select(func.count())
        .select_from(Redemption)
        .where(Redemption.customer_id == customer.id)
    )).one()

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
            # cards_count / ready_count are unused by the new C6 layout (the
            # dock cards tab covers it) — kept in the context for now so older
            # callers/snapshots don't crash. Safe to drop in a follow-up.
            "cards_count": cards_count,
            "ready_count": ready_count,
            "redemption_count": redemption_count,
            "weekday_th": weekday_th,
            "nav_inbox_badge": await _inbox_unread_count(db, customer.id),
            "text_size": customer.text_size or "md",
        },
    )


@router.get("/card/account/notifications", response_class=HTMLResponse)
async def card_account_notifications(
    request: Request,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """C6.notifications — channel preference + per-shop mute list. Anchors
    only to the C6 'การแจ้งเตือน' row. preferred_channel=None means
    waterfall (the default 'auto'); 'inbox' means deliver to in-app
    inbox only (skip push/LINE/SMS). Muted shops live in
    CustomerShopMute joined to Shop for the unmute list."""
    from app.models import CustomerShopMute
    customer, was_created = await find_or_create_customer(customer_cookie, db)
    rows = (await db.exec(
        select(CustomerShopMute, Shop)
        .join(Shop, Shop.id == CustomerShopMute.shop_id)
        .where(CustomerShopMute.customer_id == customer.id)
        .order_by(CustomerShopMute.created_at.desc())
    )).all()
    muted = [{"shop": shop} for _mute, shop in rows]

    response = templates.TemplateResponse(
        request=request,
        name="card_account_notifications.html",
        context={
            "customer": customer,
            "preferred_channel": customer.preferred_channel,
            "muted": muted,
            "nav_inbox_badge": await _inbox_unread_count(db, customer.id),
        },
    )
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


@router.post("/card/account/notifications")
async def card_account_notifications_post(
    channel: str = Form("auto"),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Save the channel preference. 'auto' clears preferred_channel
    (waterfall picks); 'inbox' pins to in-app delivery."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    customer.preferred_channel = None if channel == "auto" else channel
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
            "nav_inbox_badge": await _inbox_unread_count(db, customer.id),
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
    """C2 onboarding — 3-step Alpine flow shown on the customer's first ever
    scan (display_name IS NULL). Picks up the just-issued stamp count from
    the DB so the C2.2 stamp screen renders accurately."""
    shop = await db.get(Shop, shop_id)
    if not shop:
        return RedirectResponse(url=f"/card/{shop_id}", status_code=status.HTTP_303_SEE_OTHER)
    customer, was_created = await find_or_create_customer(customer_cookie, db)
    point_count = await active_point_count(db, shop.id, customer.id)
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
    """C9 — emotional shop story page. Reachable from the C1 daily card
    wordmark or directly. Renders thanks_message + story_text + 'opened
    N years ago' meta. Menu items + reviews are deferred until those
    models exist."""
    shop = await db.get(Shop, shop_id)
    if not shop:
        return templates.TemplateResponse(
            request=request,
            name="shop_not_found.html",
            context={"shop_id": shop_id},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    customer, was_created = await find_or_create_customer(customer_cookie, db)
    # "เปิดมา N ปี" — derived from shop.created_at. Anything <1 year says
    # "เพิ่งเปิด"; >=1 year shows the integer year count.
    from datetime import datetime, timezone
    age_days = (datetime.now(timezone.utc) - shop.created_at.replace(tzinfo=timezone.utc)).days
    years = age_days // 365
    age_label = "เพิ่งเปิด" if years < 1 else f"เปิดมา {years} ปี"
    response = templates.TemplateResponse(
        request=request,
        name="c9_story.html",
        context={
            "shop": shop,
            "customer": customer,
            "age_label": age_label,
            "nav_inbox_badge": await _inbox_unread_count(db, customer.id),
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
    shop_id: uuid.UUID,
    branch: Optional[uuid.UUID] = None,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Target of the shop's printed QR. Issues a stamp (if eligible), redirects to card view.

    Branch-specific QRs encode `?branch=<id>` so the issued stamp is tagged to that
    branch and the customer's DeeCard shows the branch name in the wordmark sub.
    """
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
    try:
        stamp = await issue_point(
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

    # First-ever scan (display_name still NULL) → C2 onboarding flow
    # (3-step welcome + reward preview + signup). Returners with display_name
    # set fall through to the regular card view celebration.
    if just_stamped and customer.display_name is None:
        redirect_url = f"/onboard/{shop_id}"
        if branch_obj:
            redirect_url += f"?branch={branch_obj.id}"
        response = RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
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

    # Guest redemption gate (per revised C4 design): full members only. The
    # guest must convert to a claimed account first — anti-fraud (no replays
    # against fresh anonymous cookies) and gives the shop a way to contact
    # the customer about the redeemed reward. Frontend renders a different
    # CTA for guests so they shouldn't reach this server-side branch, but
    # we enforce it here too in case someone POSTs directly.
    if customer.is_anonymous:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "สมัครก่อนรับรางวัลนะครับพี่ — กดปุ่ม 'สมัครรับรางวัล' เพื่อสมัครก่อน",
        )

    try:
        redemption = await redeem(db, shop, customer)
    except RedemptionError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    publish(
        shop.id,
        "feed-row",
        feed_row_html("redemption", redemption.id, bkk_feed_time(redemption.created_at), customer.display_name or "ลูกค้า"),
    )

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
        cards.append({
            "shop": shop,
            "point_count": active_count,
            "ratio": active_count / shop.reward_threshold if shop.reward_threshold else 0,
            "unread": unread_per_shop.get(shop_id, 0),
        })
    cards.sort(key=lambda c: c["ratio"], reverse=True)

    total_stamps = sum(c["point_count"] for c in cards)
    # Closest = the card with the smallest gap-to-reward (was max = farthest,
    # which read backwards in the C7 stats line). Pass the whole card
    # entry through so the template can render the shop logo + reward
    # description alongside the gap count.
    closest_card = min(
        (c for c in cards if c["point_count"] < c["shop"].reward_threshold),
        key=lambda c: c["shop"].reward_threshold - c["point_count"],
        default=None,
    )
    closest = (
        closest_card["shop"].reward_threshold - closest_card["point_count"]
        if closest_card else None
    )

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
            "total_stamps": total_stamps,
            "closest": closest,
            "closest_card": closest_card,
            "weekday_th": weekday_th,
            "inbox_unread": inbox_unread,
        },
    )
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


# ── Customer inbox (DeeReach channel "inbox" lands here) ─────────────────────

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


