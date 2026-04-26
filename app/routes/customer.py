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
from app.models import Branch, Redemption, Shop, Stamp
from app.services.auth import verify_otp
from app.services.events import feed_row_html, publish
from app.services.issuance import IssuanceError, issue_stamp
from app.services.pdpa import delete_customer_account
from app.services.redemption import RedemptionError, active_stamp_count, redeem
from app.services.soft_wall import claim_by_phone

router = APIRouter()


# Literal /card/* paths — must be registered BEFORE /card/{shop_id} so FastAPI
# doesn't try to parse "save", "account" etc. as a UUID and 422.
@router.get("/card/save", response_class=HTMLResponse)
async def soft_wall_page(request: Request):
    """C3 — Soft Wall standalone page: claim by phone OTP (or LINE — coming)."""
    return templates.TemplateResponse(request=request, name="card_save.html", context={})


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
        select(func.count(func.distinct(Stamp.shop_id)))
        .where(
            Stamp.customer_id == customer.id,
            Stamp.is_voided == False,  # noqa: E712
            Stamp.redemption_id.is_(None),
        )
    )).one()

    ready_count = 0
    stamps_per_shop = (await db.exec(
        select(Stamp.shop_id, func.count().label("active_count"))
        .where(
            Stamp.customer_id == customer.id,
            Stamp.is_voided == False,  # noqa: E712
            Stamp.redemption_id.is_(None),
        )
        .group_by(Stamp.shop_id)
    )).all()
    for shop_id, active_count in stamps_per_shop:
        shop = await db.get(Shop, shop_id)
        if shop and active_count >= shop.reward_threshold:
            ready_count += 1

    redemption_count = (await db.exec(
        select(func.count())
        .select_from(Redemption)
        .where(Redemption.customer_id == customer.id)
    )).one()

    return templates.TemplateResponse(
        request=request,
        name="card_account.html",
        context={
            "customer": customer,
            "masked_phone": _mask_phone(customer.phone),
            "cards_count": cards_count,
            "ready_count": ready_count,
            "redemption_count": redemption_count,
        },
    )


@router.post("/card/account/logout")
async def customer_logout():
    """Clear the customer cookie and bounce to home."""
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(CUSTOMER_COOKIE_NAME, path="/")
    return response


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
        select(Stamp.branch_id)
        .where(
            Stamp.shop_id == shop_id,
            Stamp.customer_id == customer_id,
            Stamp.is_voided == False,  # noqa: E712
            Stamp.branch_id.is_not(None),
        )
        .order_by(Stamp.created_at.desc())
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shop not found")

    customer, was_created = await find_or_create_customer(customer_cookie, db)
    stamp_count = await active_stamp_count(db, shop.id, customer.id)
    branch_obj = await _resolve_branch(db, shop.id, branch, customer.id)

    # First-visit detection: no redemptions yet AND only 1 active stamp.
    # Surfaces the C2 welcome banner + "save my stamps" CTA above the stamp grid.
    is_first_visit = False
    if stamp_count == 1:
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
            "stamp_count": stamp_count,
            "customer": customer,
            "is_first_visit": is_first_visit,
            "branch": branch_obj,
            # `?stamped=1` from the scan redirect — triggers the celebration
            # overlay once. Suppressed when is_first_visit (C2 banner already
            # carries the celebration energy for that case).
            "just_stamped": bool(stamped) and not is_first_visit,
        },
    )
    if was_created:
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shop not found")

    branch_obj: Optional[Branch] = None
    if branch:
        branch_obj = await db.get(Branch, branch)
        if not branch_obj or branch_obj.shop_id != shop.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Branch not found for this shop")

    customer, was_created = await find_or_create_customer(customer_cookie, db)

    just_stamped = False
    try:
        stamp = await issue_stamp(
            db, shop, customer,
            method="customer_scan",
            branch_id=branch_obj.id if branch_obj else None,
        )
        publish(
            shop.id,
            "feed-row",
            feed_row_html("stamp", stamp.id, stamp.created_at.strftime("%H:%M")),
        )
        just_stamped = True
    except IssuanceError:
        # Cooldown or other constraint — silently swallow; redirect lands on card.
        pass

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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shop not found")

    customer, _ = await find_or_create_customer(customer_cookie, db)

    try:
        redemption = await redeem(db, shop, customer)
    except RedemptionError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    publish(
        shop.id,
        "feed-row",
        feed_row_html("redemption", redemption.id, redemption.created_at.strftime("%H:%M")),
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shop not found")

    redemption = await db.get(Redemption, r)
    if not redemption or redemption.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Redemption not found")

    return templates.TemplateResponse(
        request=request,
        name="card_claimed.html",
        context={"shop": shop, "redemption": redemption},
    )


@router.post("/card/claim/phone")
async def claim_phone(
    phone: str = Form(...),
    code: str = Form(...),
    display_name: Optional[str] = Form(None),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Soft Wall: customer verifies their phone via OTP and merges/promotes the
    anonymous account to a claimed one. Refreshes the customer cookie since the
    resulting customer_id may change (merge case)."""
    if not await verify_otp(db, phone, code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired code")

    customer, _ = await find_or_create_customer(customer_cookie, db)
    claimed = await claim_by_phone(db, customer, phone, display_name=display_name)

    response = JSONResponse({"claimed": True, "customer_id": str(claimed.id)})
    set_customer_cookie(response, claimed.id)
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
    """C7 — list of every shop the (claimed) customer has stamps at."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    if customer.is_anonymous:
        # Anonymous customers can't see "my cards" — push them to claim first.
        return RedirectResponse(url="/card/save", status_code=status.HTTP_303_SEE_OTHER)

    # Active stamps grouped by shop. Same active-stamp rule as redemption service.
    stamps_per_shop = (await db.exec(
        select(Stamp.shop_id, func.count().label("active_count"))
        .where(
            Stamp.customer_id == customer.id,
            Stamp.is_voided == False,  # noqa: E712
            Stamp.redemption_id.is_(None),
        )
        .group_by(Stamp.shop_id)
    )).all()

    cards = []
    for shop_id, active_count in stamps_per_shop:
        shop = await db.get(Shop, shop_id)
        if shop is None:
            continue
        cards.append({
            "shop": shop,
            "stamp_count": active_count,
            "ratio": active_count / shop.reward_threshold if shop.reward_threshold else 0,
        })
    cards.sort(key=lambda c: c["ratio"], reverse=True)

    total_stamps = sum(c["stamp_count"] for c in cards)
    closest = max(
        ((c["shop"].reward_threshold - c["stamp_count"]) for c in cards if c["stamp_count"] < c["shop"].reward_threshold),
        default=None,
    )

    return templates.TemplateResponse(
        request=request,
        name="my_cards.html",
        context={
            "customer": customer,
            "cards": cards,
            "total_stamps": total_stamps,
            "closest": closest,
        },
    )


