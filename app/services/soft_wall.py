"""Soft Wall — anonymous Customer → claimed Customer (linked to phone, LINE, Google, or Facebook).

If a claimed customer already exists with the given identity, the anonymous customer's
stamps and redemptions are merged into the existing record and the anonymous row is
deleted. Otherwise the anonymous row is promoted in place.
"""

from typing import Optional

from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Customer, Redemption, Point


async def claim_by_phone(
    db: AsyncSession,
    anonymous_customer: Customer,
    phone: str,
    display_name: Optional[str] = None,
) -> Customer:
    """Anonymous customer provides a verified phone. Returns the resulting Customer
    (either the same row promoted, or an existing claimed row that absorbed this one)."""
    return await _claim(
        db, anonymous_customer, phone=phone, display_name=display_name
    )


async def claim_by_line(
    db: AsyncSession,
    anonymous_customer: Customer,
    line_id: str,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> Customer:
    return await _claim(
        db, anonymous_customer, line_id=line_id, display_name=display_name, picture_url=picture_url
    )


async def claim_by_google(
    db: AsyncSession,
    anonymous_customer: Customer,
    google_id: str,
    display_name: Optional[str] = None,
) -> Customer:
    return await _claim(
        db, anonymous_customer, google_id=google_id, display_name=display_name
    )


async def claim_by_facebook(
    db: AsyncSession,
    anonymous_customer: Customer,
    facebook_id: str,
    display_name: Optional[str] = None,
) -> Customer:
    return await _claim(
        db, anonymous_customer, facebook_id=facebook_id, display_name=display_name
    )


async def _claim(
    db: AsyncSession,
    anonymous_customer: Customer,
    *,
    phone: Optional[str] = None,
    line_id: Optional[str] = None,
    google_id: Optional[str] = None,
    facebook_id: Optional[str] = None,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> Customer:
    if not anonymous_customer.is_anonymous:
        return anonymous_customer  # Already claimed — no-op

    # Does a claimed customer already exist with this identity? Each call
    # site supplies exactly one identity field, so this short-circuits on
    # the first match.
    existing: Optional[Customer] = None
    if phone:
        result = await db.exec(select(Customer).where(Customer.phone == phone))
        existing = result.first()
    elif line_id:
        result = await db.exec(select(Customer).where(Customer.line_id == line_id))
        existing = result.first()
    elif google_id:
        result = await db.exec(select(Customer).where(Customer.google_id == google_id))
        existing = result.first()
    elif facebook_id:
        result = await db.exec(select(Customer).where(Customer.facebook_id == facebook_id))
        existing = result.first()

    if existing and existing.id != anonymous_customer.id:
        # Merge — move stamps + redemptions to the existing customer; delete anonymous
        await db.exec(
            update(Point)
            .where(Point.customer_id == anonymous_customer.id)
            .values(customer_id=existing.id)
        )
        await db.exec(
            update(Redemption)
            .where(Redemption.customer_id == anonymous_customer.id)
            .values(customer_id=existing.id)
        )
        if picture_url:
            existing.picture_url = picture_url
        if display_name and not existing.display_name:
            existing.display_name = display_name
        db.add(existing)
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
    if google_id:
        anonymous_customer.google_id = google_id
    if facebook_id:
        anonymous_customer.facebook_id = facebook_id
    if display_name and not anonymous_customer.display_name:
        anonymous_customer.display_name = display_name
    if picture_url:
        anonymous_customer.picture_url = picture_url
    db.add(anonymous_customer)
    await db.commit()
    await db.refresh(anonymous_customer)
    return anonymous_customer
