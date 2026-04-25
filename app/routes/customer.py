"""Customer-facing routes: scan to get a stamp, view DeeCard, Soft Wall claim."""

import uuid
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import (
    CUSTOMER_COOKIE_NAME,
    find_or_create_customer,
    set_customer_cookie,
)
from app.core.database import get_session
from app.models import Shop
from app.services.auth import verify_otp
from app.services.issuance import IssuanceError, issue_stamp
from app.services.redemption import active_stamp_count
from app.services.soft_wall import claim_by_phone

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/card/{shop_id}", response_class=HTMLResponse)
async def view_card(
    request: Request,
    shop_id: uuid.UUID,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shop not found")

    customer, was_created = await find_or_create_customer(customer_cookie, db)
    stamp_count = await active_stamp_count(db, shop.id, customer.id)

    response = templates.TemplateResponse(
        request=request,
        name=f"themes/{shop.theme_name}.html",
        context={"shop": shop, "stamp_count": stamp_count, "customer": customer},
    )
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


@router.get("/scan/{shop_id}")
async def scan(
    shop_id: uuid.UUID,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Target of the shop's printed QR. Issues a stamp (if eligible), redirects to card view."""
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shop not found")

    customer, was_created = await find_or_create_customer(customer_cookie, db)

    try:
        await issue_stamp(db, shop, customer, method="customer_scan")
    except IssuanceError:
        # Daily cap or other constraint — silently swallow; redirect lands on card.
        pass

    response = RedirectResponse(url=f"/card/{shop_id}", status_code=status.HTTP_303_SEE_OTHER)
    if was_created:
        set_customer_cookie(response, customer.id)
    return response


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
