"""Staff-side issuance: shop_scan / phone_entry, and the 60-second voids."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import (
    SessionContext,
    get_current_shop,
    get_current_staff,
    require_permission,
)
from app.core.database import get_session
from app.models import Customer, Redemption, Shop, Stamp, StaffMember
from app.models.util import utcnow
from app.services.issuance import IssuanceError, issue_stamp, void_stamp
from app.services.redemption import void_redemption

router = APIRouter()

VOID_WINDOW_SECONDS = 60


@router.post("/issue")
async def staff_issue_stamp(
    method: str = Form(...),
    customer_id: Optional[UUID] = Form(None),
    phone: Optional[str] = Form(None),
    branch_id: Optional[UUID] = Form(None),
    shop: Shop = Depends(get_current_shop),
    staff: Optional[StaffMember] = Depends(get_current_staff),
    db: AsyncSession = Depends(get_session),
):
    """Issue a stamp via shop_scan (provide customer_id) or phone_entry (provide phone).

    For phone_entry, a Customer is created if none exists for that phone (claimed account).
    """
    if method == "shop_scan":
        if not customer_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "customer_id is required for shop_scan")
        customer = await db.get(Customer, customer_id)
        if not customer:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    elif method == "phone_entry":
        if not phone:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "phone is required for phone_entry")
        result = await db.exec(select(Customer).where(Customer.phone == phone))
        customer = result.first()
        if not customer:
            customer = Customer(is_anonymous=False, phone=phone)
            db.add(customer)
            await db.commit()
            await db.refresh(customer)
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "method must be 'shop_scan' or 'phone_entry'"
        )

    try:
        stamp = await issue_stamp(
            db, shop, customer,
            method=method,
            branch_id=branch_id,
            staff_id=staff.id if staff else None,
        )
    except IssuanceError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    return {"stamp_id": str(stamp.id), "customer_id": str(customer.id)}


@router.post("/stamps/{stamp_id}/void")
async def void_stamp_route(
    stamp_id: UUID,
    ctx: SessionContext = Depends(require_permission("can_void")),
    db: AsyncSession = Depends(get_session),
):
    stamp = await db.get(Stamp, stamp_id)
    if not stamp:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stamp not found")
    if stamp.shop_id != ctx.shop_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-shop access denied")

    age_seconds = (utcnow() - stamp.created_at).total_seconds()
    if age_seconds > VOID_WINDOW_SECONDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Void window expired ({VOID_WINDOW_SECONDS}s)",
        )

    await void_stamp(db, stamp, by_staff_id=ctx.staff_id)
    return {"voided": True, "stamp_id": str(stamp.id)}


@router.post("/redemptions/{redemption_id}/void")
async def void_redemption_route(
    redemption_id: UUID,
    ctx: SessionContext = Depends(require_permission("can_void")),
    db: AsyncSession = Depends(get_session),
):
    redemption = await db.get(Redemption, redemption_id)
    if not redemption:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Redemption not found")
    if redemption.shop_id != ctx.shop_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-shop access denied")

    age_seconds = (utcnow() - redemption.created_at).total_seconds()
    if age_seconds > VOID_WINDOW_SECONDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Void window expired ({VOID_WINDOW_SECONDS}s)",
        )

    await void_redemption(db, redemption, by_staff_id=ctx.staff_id)
    return {"voided": True, "redemption_id": str(redemption.id)}
