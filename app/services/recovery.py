"""Recovery codes for anonymous customers.

When a customer skips signup (C2.3 → C2.4), we issue them a 12-character
human-readable recovery code so they can re-attach their points on a new
device. Alphabet drops 0/O/1/I/L to avoid mis-reading screenshots.
"""

import secrets
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Customer

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _generate() -> str:
    """Format: XXXX-XXXX-XXXX (12 chars + 2 hyphens, all uppercase)."""
    chars = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return f"{chars[0:4]}-{chars[4:8]}-{chars[8:12]}"


async def ensure_recovery_code(db: AsyncSession, customer: Customer) -> str:
    """Return the customer's recovery code, creating one if missing.

    Generation collision-retries against a unique index — in practice with a
    31^12 keyspace this almost never loops.
    """
    if customer.recovery_code:
        return customer.recovery_code

    for _ in range(8):
        candidate = _generate()
        clash = (await db.exec(
            select(Customer).where(Customer.recovery_code == candidate)
        )).first()
        if clash is None:
            customer.recovery_code = candidate
            db.add(customer)
            await db.commit()
            await db.refresh(customer)
            return candidate
    raise RuntimeError("could not allocate unique recovery code after 8 tries")


def normalize(raw: str) -> str:
    """User input → canonical form. Strips whitespace, uppercases, inserts
    hyphens at every 4th char if the user typed without them."""
    cleaned = "".join(c for c in raw.strip().upper() if c.isalnum())
    if len(cleaned) != 12:
        return ""
    return f"{cleaned[0:4]}-{cleaned[4:8]}-{cleaned[8:12]}"


async def find_by_code(db: AsyncSession, raw: str) -> Optional[Customer]:
    code = normalize(raw)
    if not code:
        return None
    return (await db.exec(
        select(Customer).where(Customer.recovery_code == code)
    )).first()
