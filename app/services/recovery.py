"""Recovery codes for anonymous customers.

When a customer skips signup (C2.3 → C2.4), we issue them a 12-digit
recovery code so they can re-attach their points on a new device.
Format: XXXX-XXXX-XXXX. Stored on the User row (recovery_code is part
of identity, same as the four provider columns).
"""

import secrets
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Customer, User

ALPHABET = "0123456789"


def _generate() -> str:
    """Format: XXXX-XXXX-XXXX (12 digits + 2 hyphens)."""
    chars = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return f"{chars[0:4]}-{chars[4:8]}-{chars[8:12]}"


async def ensure_recovery_code(db: AsyncSession, customer: Customer) -> str:
    """Return the customer's recovery code, creating one if missing.

    Generation collision-retries against a unique index — in practice with a
    31^12 keyspace this almost never loops.
    """
    if customer.user.recovery_code:
        return customer.user.recovery_code

    for _ in range(8):
        candidate = _generate()
        clash = (await db.exec(
            select(User).where(User.recovery_code == candidate)
        )).first()
        if clash is None:
            customer.user.recovery_code = candidate
            db.add(customer.user)
            await db.commit()
            await db.refresh(customer.user)
            return candidate
    raise RuntimeError("could not allocate unique recovery code after 8 tries")


def normalize(raw: str) -> str:
    """User input → canonical form. Strips whitespace, filters to digits,
    inserts hyphens at every 4th char if the user typed without them."""
    cleaned = "".join(c for c in raw.strip() if c.isdigit())
    if len(cleaned) != 12:
        return ""
    return f"{cleaned[0:4]}-{cleaned[4:8]}-{cleaned[8:12]}"


async def find_by_code(db: AsyncSession, raw: str) -> Optional[Customer]:
    code = normalize(raw)
    if not code:
        return None
    return (await db.exec(
        select(Customer).join(User, Customer.user_id == User.id)
        .where(User.recovery_code == code)
    )).first()
