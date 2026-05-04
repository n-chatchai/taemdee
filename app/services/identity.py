"""Shared identity helpers — used by both customer (Customer rows) and
shop (StaffMember rows) for the four sign-in providers.

Both tables carry the same four columns (line_id, google_id,
facebook_id, phone) so the find/bind/unbind logic is identical except
for the model class and a few callsite-specific concerns (extra
filters, conflict copy, last-identity guard set). This module owns
the generic mechanics; `services/soft_wall.py` (customer-side) and
`services/team.py` (shop-side) wrap them with role-specific glue.
"""

from typing import Optional, Sequence, Type

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


async def find_row_by_provider(
    db: AsyncSession,
    model: Type,
    provider: str,
    ext_id: str,
    *,
    extra_filters: Sequence = (),
):
    """SELECT 1 row WHERE <model>.<provider_field> = ext_id. `extra_filters`
    lets staff lookups scope to non-revoked rows.
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
    row,
    provider: str,
    ext_id: str,
    *,
    model: Type,
    conflict_message: str,
    extra_filters: Sequence = (),
) -> object:
    """Set `row.<provider_field> = ext_id`. Raises IdentityConflict if the
    same provider id is already bound to a *different* row of the same
    model. No-op if already on this row.
    """
    field = _provider_field(provider)
    if getattr(row, field) == ext_id:
        return row

    other = await find_row_by_provider(
        db, model, provider, ext_id, extra_filters=extra_filters
    )
    if other is not None and getattr(other, "id", None) != getattr(row, "id", None):
        raise IdentityConflict(conflict_message)

    setattr(row, field, ext_id)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


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
