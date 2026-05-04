"""Soft Wall — anonymous Customer → claimed Customer.

Identity (line_id / google_id / facebook_id / phone / display_name /
picture_url / recovery_code) lives on User, not Customer. A claim or
link goes through the customer's User row. When the provider id
already belongs to a different User, services/identity.merge_users
folds the two into one — same person across roles.

Public surface:
  - claim_by_provider(db, anon, provider, ext_id, ...) — anonymous
    customer claims via a provider. Returns the resulting Customer
    (either the same row promoted, or a different existing customer
    that absorbed this one).
  - link_to_claimed(db, customer, *, provider, ext_id, ...) — claimed
    customer adds a second identity. Same behaviour as claim but
    starts from a non-anonymous row.
  - disconnect_provider(db, customer, provider) — unlink one identity.
    Last-identity guard ensures the customer can still log back in.
"""

from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Customer, User
from app.services.identity import (
    PROVIDER_FIELDS,
    IdentityConflict,
    bind_provider,
    find_user_by_provider,
    merge_users,
    unbind_provider,
)


# Customer identity field set — recovery_code counts as an identity
# for the unlink-last-id guard. All five live on User now.
_CUSTOMER_IDENTITY_FIELDS = (
    "line_id", "google_id", "facebook_id", "phone", "recovery_code",
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
    """Anonymous customer claims via any of the four providers.

    If another User already owns this provider id, the anonymous
    customer's User is merged INTO that existing User (the merge
    direction matters: existing has history, anonymous is empty;
    points and the customer profile move into existing's customer).
    Returns the existing customer in that case.

    Otherwise the anonymous user is promoted in place — the provider
    field is set on its User and is_anonymous flipped to False.
    """
    if not anonymous_customer.is_anonymous:
        return anonymous_customer

    field = PROVIDER_FIELDS[provider]
    user = anonymous_customer.user

    existing_user = await find_user_by_provider(db, User, provider, ext_id)
    if existing_user is not None and existing_user.id != user.id:
        # Merge our anonymous user INTO the existing one. After this
        # call:
        #   - the anonymous user is gone
        #   - existing_user holds the merged identity
        #   - the points + redemptions etc. that lived on the
        #     anonymous customer have moved to existing's customer (if
        #     it had one) — or the anonymous customer was reassigned
        #     to existing's user_id.
        await merge_users(db, source=user, target=existing_user)
        await db.refresh(existing_user)

        # Pick up display_name / picture_url if the existing identity
        # didn't have them (LINE profile vs first-time existing row).
        changed = False
        if display_name and not existing_user.display_name:
            existing_user.display_name = display_name
            changed = True
        if picture_url and not existing_user.picture_url:
            existing_user.picture_url = picture_url
            changed = True
        if changed:
            db.add(existing_user)
            await db.commit()

        # Find the customer profile for the merged user (could be the
        # original existing's customer with anonymous's data folded
        # in, or the promoted anonymous customer reassigned to
        # existing.user_id when existing had no customer).
        merged_cust = (await db.exec(
            select(Customer).where(Customer.user_id == existing_user.id)
        )).first()
        if merged_cust is not None:
            if merged_cust.is_anonymous:
                merged_cust.is_anonymous = False
                db.add(merged_cust)
                await db.commit()
                await db.refresh(merged_cust)
            return merged_cust
        # No customer at all on existing user (only StaffMember). The
        # anonymous customer was reassigned to existing.user_id; find
        # it and flip is_anonymous.
        # (anonymous_customer's user_id changed inside merge_users)
        await db.refresh(anonymous_customer)
        anonymous_customer.is_anonymous = False
        db.add(anonymous_customer)
        await db.commit()
        return anonymous_customer

    # No conflict — promote the anonymous user in place.
    setattr(user, field, ext_id)
    if display_name and not user.display_name:
        user.display_name = display_name
    if picture_url:
        user.picture_url = picture_url
    anonymous_customer.is_anonymous = False
    db.add(user)
    db.add(anonymous_customer)
    await db.commit()
    await db.refresh(anonymous_customer)
    return anonymous_customer


# Back-compat wrappers (existing routes still call these).
async def claim_by_phone(
    db, anonymous_customer, phone, display_name=None,
):
    return await claim_by_provider(
        db, anonymous_customer, "phone", phone, display_name=display_name,
    )


async def claim_by_line(
    db, anonymous_customer, line_id, display_name=None, picture_url=None,
):
    return await claim_by_provider(
        db, anonymous_customer, "line", line_id,
        display_name=display_name, picture_url=picture_url,
    )


async def claim_by_google(
    db, anonymous_customer, google_id, display_name=None,
):
    return await claim_by_provider(
        db, anonymous_customer, "google", google_id, display_name=display_name,
    )


async def claim_by_facebook(
    db, anonymous_customer, facebook_id, display_name=None,
):
    return await claim_by_provider(
        db, anonymous_customer, "facebook", facebook_id, display_name=display_name,
    )


# ── link 2nd provider (already claimed) ────────────────────────────────────


async def link_to_claimed(
    db: AsyncSession,
    customer: Customer,
    *,
    provider: Optional[str] = None,
    ext_id: Optional[str] = None,
    # Legacy kwargs — kept for callsites that haven't migrated.
    line_id: Optional[str] = None,
    google_id: Optional[str] = None,
    facebook_id: Optional[str] = None,
    phone: Optional[str] = None,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> Customer:
    """Add a 2nd provider to a claimed customer's User. If the
    provider id is already on a different User, that User is merged
    into customer.user (bind_provider's default behaviour now —
    no IdentityConflict). Returns the customer (id unchanged when
    no merge fires; reassigned to the surviving user when one does).
    """
    if customer.is_anonymous:
        return customer

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

    await bind_provider(db, customer.user, provider, ext_id)
    user = customer.user

    if display_name and not user.display_name:
        user.display_name = display_name
    if picture_url and not user.picture_url:
        user.picture_url = picture_url
    db.add(user)
    await db.commit()
    await db.refresh(customer)
    return customer


# ── disconnect ──────────────────────────────────────────────────────────────


async def disconnect_provider(
    db: AsyncSession,
    customer: Customer,
    provider: str,
) -> Customer:
    """Unlink one identity from the customer's User. Refuses to remove
    the last reachable identity — recovery_code counts.
    """
    await unbind_provider(
        db, customer.user, provider,
        identity_fields=_CUSTOMER_IDENTITY_FIELDS,
        last_identity_message=_LAST_IDENTITY,
    )
    await db.refresh(customer)
    return customer
