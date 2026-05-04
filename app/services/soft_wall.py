"""Soft Wall — anonymous Customer → claimed Customer (linked to phone, LINE, Google, or Facebook).

If a claimed customer already exists with the given identity, the anonymous customer's
stamps and redemptions are merged into the existing record and the anonymous row is
deleted. Otherwise the anonymous row is promoted in place.

For an already-claimed customer adding a *second* identity (e.g. claimed
via LINE, now also wants Google), `link_to_claimed` is the dedicated
path — it refuses to silently merge into another existing customer
because that would be destructive without confirmation.
"""

from typing import Optional

from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Customer, Redemption, Point


class IdentityConflict(Exception):
    """Raised when a link/disconnect can't proceed safely."""


# Identity fields we can link/unlink. Order isn't significant; this is
# the canonical list for "is the customer reachable by anything?" checks
# below. recovery_code counts as an identity for the unlink-last-id guard
# because the customer can still recover via /recover with it.
_IDENTITY_FIELDS = ("line_id", "google_id", "facebook_id", "phone", "recovery_code")


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


async def link_to_claimed(
    db: AsyncSession,
    customer: Customer,
    *,
    line_id: Optional[str] = None,
    google_id: Optional[str] = None,
    facebook_id: Optional[str] = None,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> Customer:
    """Add a second identity to an already-claimed customer (e.g. they
    claimed via LINE, now want Google too). Refuses to merge into a
    different existing customer — that's destructive without explicit
    consent and surfaces as IdentityConflict so the route can render
    a clear "ใช้บัญชีอื่นถ้าต้องการสลับ" message.

    Exactly one identity field should be supplied per call (matches the
    OAuth callback shape). Other identities on `customer` are left
    alone.
    """
    if customer.is_anonymous:
        # Defensive — claim() should be called first for anonymous rows.
        # Treat this like a no-op rather than silently switching paths.
        return customer

    # Bail out if the identity already lives on someone else's account.
    other: Optional[Customer] = None
    if line_id:
        other = (await db.exec(
            select(Customer).where(Customer.line_id == line_id)
        )).first()
    elif google_id:
        other = (await db.exec(
            select(Customer).where(Customer.google_id == google_id)
        )).first()
    elif facebook_id:
        other = (await db.exec(
            select(Customer).where(Customer.facebook_id == facebook_id)
        )).first()
    if other is not None and other.id != customer.id:
        raise IdentityConflict(
            "บัญชีนี้ผูกกับลูกค้ารายอื่นอยู่แล้ว · "
            "ใช้บัญชีอื่นถ้าต้องการสลับเข้าระบบ"
        )

    # Same identity already on this customer — re-link is a no-op.
    if (
        (line_id and customer.line_id == line_id)
        or (google_id and customer.google_id == google_id)
        or (facebook_id and customer.facebook_id == facebook_id)
    ):
        return customer

    if line_id:
        customer.line_id = line_id
    if google_id:
        customer.google_id = google_id
    if facebook_id:
        customer.facebook_id = facebook_id
    if picture_url and not customer.picture_url:
        customer.picture_url = picture_url
    if display_name and not customer.display_name:
        customer.display_name = display_name
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return customer


async def disconnect_provider(
    db: AsyncSession,
    customer: Customer,
    provider: str,
) -> Customer:
    """Unlink one identity from a claimed customer. Refuses to remove
    the last reachable identity — IdentityConflict is raised so the
    customer doesn't end up with a row no one can log into.

    `provider` is one of "line", "google", "facebook", "phone".
    """
    field = {
        "line": "line_id",
        "google": "google_id",
        "facebook": "facebook_id",
        "phone": "phone",
    }.get(provider)
    if field is None:
        raise IdentityConflict("ผู้ให้บริการไม่ถูกต้อง")
    if getattr(customer, field) is None:
        # Already unlinked — nothing to do.
        return customer

    # Last-identity guard: count what survives if we drop this field.
    remaining = sum(
        1
        for f in _IDENTITY_FIELDS
        if f != field and getattr(customer, f, None)
    )
    if remaining == 0:
        raise IdentityConflict(
            "ปลดเชื่อมไม่ได้ · ต้องเหลืออย่างน้อย 1 ช่องทางเพื่อเข้าสู่ระบบครั้งหน้า"
        )

    setattr(customer, field, None)
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return customer
