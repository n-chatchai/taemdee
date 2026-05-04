"""Shared identity helpers — provider-id ops on the User table.

Identity (line_id, google_id, facebook_id, phone, display_name, etc.)
lives on User. Customer + StaffMember are role profiles that point at
a User via user_id FK. find_user_by_provider / bind_provider /
unbind_provider operate on User rows; soft_wall and team wrap them
with role-specific glue (anonymous-merge for customers, accept-invite
+ owner-staff backfill for shop-side).

When binding a provider id that's already on a *different* User row,
bind_provider absorbs the other User into the active one via
merge_users — same person signing in via a 2nd provider that
historically created a separate User now gets folded into one
identity, no IdentityConflict thrown.
"""

from typing import Optional, Sequence, Type

from sqlalchemy import update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


# Single source of truth for "provider name" → "column on the row".
# Adding a new social provider means one entry here + a column on
# Customer + StaffMember (+ migration).
PROVIDER_FIELDS: dict[str, str] = {
    "line": "line_id",
    "google": "google_id",
    "facebook": "facebook_id",
    "phone": "phone",
}


class IdentityConflict(Exception):
    """Raised when a link/disconnect can't proceed safely:

    - link: the provider id is already on a different row
    - unlink: the field is the row's last reachable identity
    - any: the provider name isn't recognised

    Routes catch this and surface as 409 / friendly error.
    """


def _provider_field(provider: str) -> str:
    field = PROVIDER_FIELDS.get(provider)
    if field is None:
        raise IdentityConflict("ผู้ให้บริการไม่ถูกต้อง")
    return field


async def find_user_by_provider(
    db: AsyncSession,
    model: Type,
    provider: str,
    ext_id: str,
    *,
    extra_filters: Sequence = (),
):
    """SELECT 1 row WHERE <model>.<provider_field> = ext_id. The single
    "find by provider id" primitive — Customer and StaffMember both
    carry the four identity columns (line_id / google_id / facebook_id
    / phone), so one function works for both. `extra_filters` lets
    callers scope (e.g. StaffMember.revoked_at IS NULL).
    """
    if not ext_id:
        return None
    field = _provider_field(provider)
    column = getattr(model, field)
    stmt = select(model).where(column == ext_id)
    for f in extra_filters:
        stmt = stmt.where(f)
    return (await db.exec(stmt)).first()


async def bind_provider(
    db: AsyncSession,
    user,
    provider: str,
    ext_id: str,
    *,
    extra_filters: Sequence = (),
) -> object:
    """Set `user.<provider_field> = ext_id` on a User row. If the same
    provider id is already bound to a *different* User, that other
    User is absorbed into `user` via merge_users — provider columns
    + role profiles + relationships migrate over, then the source
    User row is deleted. No IdentityConflict raised by default —
    "same person, second provider" should Just Work, the merge is
    the whole point of having a User table.

    No-op if `user` already owns this ext_id.
    """
    from app.models import User

    field = _provider_field(provider)
    if getattr(user, field) == ext_id:
        return user

    other = await find_user_by_provider(
        db, User, provider, ext_id, extra_filters=extra_filters
    )
    if other is not None and getattr(other, "id", None) != getattr(user, "id", None):
        # Absorb the other User into this one. After merge, the
        # provider id is on `user` and the source row is gone.
        await merge_users(db, source=other, target=user)
        # merge_users already commits and copies the provider field
        # across (when target's slot was empty), so we're done.
        await db.refresh(user)
        if getattr(user, field) == ext_id:
            return user

    setattr(user, field, ext_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def merge_users(
    db: AsyncSession,
    *,
    source,
    target,
) -> object:
    """Fold `source` User into `target` User and delete the source row.

    What moves:
      - Identity fields: every provider column / display_name /
        picture_url / recovery_code / etc. that's set on source AND
        empty on target gets copied over. Target's existing values
        win on conflict (active session is canonical).
      - Customer profile: at most one per User. If both have one,
        merge by reassigning Points / Redemptions / Inbox /
        CustomerItem / CustomerShopMute from source's customer to
        target's customer, then delete source's customer. If only
        source has one, reassign its user_id to target.
      - StaffMember rows: always reassign user_id (a person can be
        staff at multiple shops, so duplicates by shop_id are fine —
        the same shop can't have two owner-staff because that's
        guarded elsewhere).

    Then: delete source User. The caller's `target` is left with a
    populated row that needs a refresh.
    """
    from app.models import (
        Customer, CustomerItem, CustomerShopMute,
        Inbox, Point, Redemption, StaffMember, User,
    )

    if getattr(source, "id", None) == getattr(target, "id", None):
        return target

    # ── Identity fields: copy source → target only where target is empty.
    _IDENTITY_COLUMNS = (
        "line_id", "google_id", "facebook_id", "phone",
        "display_name", "picture_url", "recovery_code",
        "line_friend_status", "line_messaging_blocked_at",
        "text_size",
        "web_push_endpoint", "web_push_p256dh", "web_push_auth",
    )
    for col in _IDENTITY_COLUMNS:
        if getattr(target, col, None) in (None, "") and getattr(source, col, None) not in (None, ""):
            setattr(target, col, getattr(source, col))
            # Clear on source so the unique-column constraint doesn't
            # block delete (e.g. line_id UNIQUE — can't have two rows
            # both holding the same value mid-merge).
            setattr(source, col, None)
        elif getattr(source, col, None) is not None:
            # Target already has a value here — clear source to free
            # the unique slot before delete.
            if col in ("line_id", "google_id", "facebook_id", "phone", "recovery_code"):
                setattr(source, col, None)
    # Booleans we OR rather than copy — if either side ever installed
    # the PWA / accepted notifications, the merged identity should
    # remember.
    target.is_pwa = bool(target.is_pwa or source.is_pwa)
    target.notifications_enabled = bool(
        target.notifications_enabled and source.notifications_enabled
    )
    db.add(target)
    db.add(source)
    await db.flush()

    # ── Customer profile reconciliation
    src_cust = (await db.exec(
        select(Customer).where(Customer.user_id == source.id)
    )).first()
    tgt_cust = (await db.exec(
        select(Customer).where(Customer.user_id == target.id)
    )).first()

    if src_cust is not None and tgt_cust is not None:
        # Both sides have a customer profile — fold src into tgt.
        for child_model, fk in (
            (Point, Point.customer_id),
            (Redemption, Redemption.customer_id),
            (Inbox, Inbox.customer_id),
            (CustomerItem, CustomerItem.customer_id),
            (CustomerShopMute, CustomerShopMute.customer_id),
        ):
            await db.exec(
                update(child_model)
                .where(fk == src_cust.id)
                .values(customer_id=tgt_cust.id)
            )
        # Anonymous flag: if either side was claimed (is_anonymous=False),
        # the merged customer is claimed.
        if not src_cust.is_anonymous:
            tgt_cust.is_anonymous = False
        db.add(tgt_cust)
        # Soft-delete: don't drop src row. Mark merged_into_id so any
        # stale cookie still pointing at src can transparently resolve
        # to tgt via find_or_create_customer's chain follow. Critical
        # for the iOS PWA OAuth case where the callback runs in Safari
        # and can't update the PWA's cookie.
        src_cust.merged_into_id = tgt_cust.id
        src_cust.user_id = target.id  # break the FK to-be-deleted source user
        db.add(src_cust)
    elif src_cust is not None:
        # Only source has a customer — reassign FK to target.
        src_cust.user_id = target.id
        db.add(src_cust)
    # If only target has one, nothing to do.

    # ── StaffMember rows: always reassign to target. The same person
    # can be staff at multiple shops, so duplicates by shop are fine.
    await db.exec(
        update(StaffMember)
        .where(StaffMember.user_id == source.id)
        .values(user_id=target.id)
    )

    await db.flush()
    await db.delete(source)
    await db.commit()
    return target


async def unbind_provider(
    db: AsyncSession,
    row,
    provider: str,
    *,
    identity_fields: Sequence[str],
    last_identity_message: str,
) -> object:
    """Clear `row.<provider_field>`. Refuses if the field is the row's only
    remaining identity (counts entries in `identity_fields` other than
    this one). No-op if already None.
    """
    field = _provider_field(provider)
    if getattr(row, field) is None:
        return row

    remaining = sum(
        1
        for f in identity_fields
        if f != field and getattr(row, f, None)
    )
    if remaining == 0:
        raise IdentityConflict(last_identity_message)

    setattr(row, field, None)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row
