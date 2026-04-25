"""Soft Wall — anonymous Customer → claimed Customer (linked to LINE or phone).

If a claimed customer already exists with the given identity, the anonymous customer's
stamps and redemptions are merged into the existing record and the anonymous row is
deleted. Otherwise the anonymous row is promoted in place.
"""

from typing import Optional

from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Customer, Redemption, Stamp


async def claim_by_phone(
    db: AsyncSession,
    anonymous_customer: Customer,
    phone: str,
    display_name: Optional[str] = None,
) -> Customer:
    """Anonymous customer provides a verified phone. Returns the resulting Customer
    (either the same row promoted, or an existing claimed row that absorbed this one)."""
    return await _claim(
        db, anonymous_customer, phone=phone, line_id=None, display_name=display_name
    )


async def claim_by_line(
    db: AsyncSession,
    anonymous_customer: Customer,
    line_id: str,
    display_name: Optional[str] = None,
) -> Customer:
    return await _claim(
        db, anonymous_customer, phone=None, line_id=line_id, display_name=display_name
    )


async def _claim(
    db: AsyncSession,
    anonymous_customer: Customer,
    phone: Optional[str],
    line_id: Optional[str],
    display_name: Optional[str],
) -> Customer:
    if not anonymous_customer.is_anonymous:
        return anonymous_customer  # Already claimed — no-op

    # Does a claimed customer already exist with this identity?
    existing: Optional[Customer] = None
    if phone:
        result = await db.exec(select(Customer).where(Customer.phone == phone))
        existing = result.first()
    elif line_id:
        result = await db.exec(select(Customer).where(Customer.line_id == line_id))
        existing = result.first()

    if existing and existing.id != anonymous_customer.id:
        # Merge — move stamps + redemptions to the existing customer; delete anonymous
        await db.exec(
            update(Stamp)
            .where(Stamp.customer_id == anonymous_customer.id)
            .values(customer_id=existing.id)
        )
        await db.exec(
            update(Redemption)
            .where(Redemption.customer_id == anonymous_customer.id)
            .values(customer_id=existing.id)
        )
        await db.delete(anonymous_customer)
        await db.commit()
        await db.refresh(existing)
        return existing

    # Promote in place
    anonymous_customer.is_anonymous = False
    if phone:
        anonymous_customer.phone = phone
    if line_id:
        anonymous_customer.line_id = line_id
    if display_name:
        anonymous_customer.display_name = display_name
    db.add(anonymous_customer)
    await db.commit()
    await db.refresh(anonymous_customer)
    return anonymous_customer
