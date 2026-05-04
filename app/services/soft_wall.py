"""Soft Wall — anonymous Customer → claimed Customer (linked to phone, LINE, Google, or Facebook).

If a claimed customer already exists with the given identity, the anonymous customer's
stamps and redemptions are merged into the existing record and the anonymous row is
deleted. Otherwise the anonymous row is promoted in place.

For an already-claimed customer adding a *second* identity (e.g. claimed
via LINE, now also wants Google), `link_to_claimed` is the dedicated
path — it refuses to silently merge into another existing customer
because that would be destructive without confirmation.

Provider-keyed helpers (claim_by_provider, link_to_claimed,
disconnect_provider) live in services/identity.py — this module wraps
them with customer-side glue (anonymous-merge, recovery_code in the
last-identity guard, on-claim picture/display name copy).
"""

from typing import Optional

from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Customer, Redemption, Point
from app.services.identity import (
    PROVIDER_FIELDS,
    IdentityConflict,
    bind_provider,
    find_row_by_provider,
    unbind_provider,
)


# Customer identity field set — recovery_code counts as an identity for
# the unlink-last-id guard (the customer can still reach their account
# via /recover with it).
_CUSTOMER_IDENTITY_FIELDS = (
    "line_id", "google_id", "facebook_id", "phone", "recovery_code",
)
_LINK_CONFLICT = (
    "บัญชีนี้ผูกกับลูกค้ารายอื่นอยู่แล้ว · "
    "ใช้บัญชีอื่นถ้าต้องการสลับเข้าระบบ"
)
_LAST_IDENTITY = (
    "ปลดเชื่อมไม่ได้ · ต้องเหลืออย่างน้อย 1 ช่องทางเพื่อเข้าสู่ระบบครั้งหน้า"
)


# ── claim (anonymous → claimed) ─────────────────────────────────────────────


async def claim_by_provider(
    db: AsyncSession,
    anonymous_customer: Customer,
    provider: str,
    ext_id: str,
    *,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> Customer:
    """Anonymous customer claims via any of the four providers. Merges
    into an existing claimed row when one already owns this identity;
    otherwise promotes the anonymous row in place.
    """
    if not anonymous_customer.is_anonymous:
        return anonymous_customer

    existing = await find_row_by_provider(db, Customer, provider, ext_id)
    field = PROVIDER_FIELDS[provider]

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
    setattr(anonymous_customer, field, ext_id)
    if display_name and not anonymous_customer.display_name:
        anonymous_customer.display_name = display_name
    if picture_url:
        anonymous_customer.picture_url = picture_url
    db.add(anonymous_customer)
    await db.commit()
    await db.refresh(anonymous_customer)
    return anonymous_customer


# Back-compat wrappers for the four claim_by_<provider>() callsites in
# routes/auth.py + routes/customer.py. New callers should hit
# claim_by_provider directly.

async def claim_by_phone(
    db: AsyncSession,
    anonymous_customer: Customer,
    phone: str,
    display_name: Optional[str] = None,
) -> Customer:
    return await claim_by_provider(
        db, anonymous_customer, "phone", phone, display_name=display_name,
    )


async def claim_by_line(
    db: AsyncSession,
    anonymous_customer: Customer,
    line_id: str,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> Customer:
    return await claim_by_provider(
        db, anonymous_customer, "line", line_id,
        display_name=display_name, picture_url=picture_url,
    )


async def claim_by_google(
    db: AsyncSession,
    anonymous_customer: Customer,
    google_id: str,
    display_name: Optional[str] = None,
) -> Customer:
    return await claim_by_provider(
        db, anonymous_customer, "google", google_id, display_name=display_name,
    )


async def claim_by_facebook(
    db: AsyncSession,
    anonymous_customer: Customer,
    facebook_id: str,
    display_name: Optional[str] = None,
) -> Customer:
    return await claim_by_provider(
        db, anonymous_customer, "facebook", facebook_id, display_name=display_name,
    )


# ── link 2nd provider (claimed → still claimed, now also bound to X) ────────


async def link_to_claimed(
    db: AsyncSession,
    customer: Customer,
    *,
    provider: Optional[str] = None,
    ext_id: Optional[str] = None,
    # Legacy keyword form — kept so existing callsites don't break.
    line_id: Optional[str] = None,
    google_id: Optional[str] = None,
    facebook_id: Optional[str] = None,
    phone: Optional[str] = None,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> Customer:
    """Add a 2nd identity to a claimed customer. Refuses to merge into a
    different customer — IdentityConflict surfaces and the route renders
    a "ใช้บัญชีอื่น" message.

    Accepts either the new (provider, ext_id) form or the legacy
    line_id/google_id/facebook_id/phone keyword form. Phone was missing
    from the legacy keyword form — adding it here closes the audit gap
    where /card/claim/phone for an already-claimed customer fell back to
    the anonymous-claim path and could merge them into a different row.
    """
    if customer.is_anonymous:
        return customer

    # Resolve (provider, ext_id) from legacy kwargs if needed.
    if provider is None or ext_id is None:
        legacy = {
            "line": line_id, "google": google_id,
            "facebook": facebook_id, "phone": phone,
        }
        for p, v in legacy.items():
            if v:
                provider, ext_id = p, v
                break
    if provider is None or not ext_id:
        return customer

    customer = await bind_provider(
        db, customer, provider, ext_id,
        model=Customer, conflict_message=_LINK_CONFLICT,
    )

    if display_name and not customer.display_name:
        customer.display_name = display_name
    if picture_url and not customer.picture_url:
        customer.picture_url = picture_url
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return customer


# ── disconnect ──────────────────────────────────────────────────────────────


async def disconnect_provider(
    db: AsyncSession,
    customer: Customer,
    provider: str,
) -> Customer:
    """Unlink one identity. Refuses to remove the last reachable one —
    recovery_code counts as a fallback identity for the guard.
    """
    return await unbind_provider(
        db, customer, provider,
        identity_fields=_CUSTOMER_IDENTITY_FIELDS,
        last_identity_message=_LAST_IDENTITY,
    )
