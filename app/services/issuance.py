"""Stamp issuance — the core act across all 3 methods, plus void.

Anti-rescan policy is per-shop: `Shop.scan_cooldown_minutes` (default 0 = no
cooldown). When > 0, a customer must wait that many minutes between stamps at
the same shop. Geofence + pattern alerts are deferred.
"""

from datetime import timedelta
from typing import Optional
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Customer, Shop, Stamp
from app.models.util import utcnow

VALID_METHODS = {"customer_scan", "shop_scan", "phone_entry", "system"}


class IssuanceError(Exception):
    pass


async def _has_recent_stamp(
    db: AsyncSession, shop_id: UUID, customer_id: UUID, since,
) -> bool:
    """True if a non-voided stamp exists for (shop, customer) at or after `since`."""
    result = await db.exec(
        select(Stamp.id)
        .where(
            Stamp.shop_id == shop_id,
            Stamp.customer_id == customer_id,
            Stamp.created_at >= since,
            Stamp.is_voided == False,  # noqa: E712
        )
        .limit(1)
    )
    return result.first() is not None


async def issue_stamp(
    db: AsyncSession,
    shop: Shop,
    customer: Customer,
    method: str,
    *,
    branch_id: Optional[UUID] = None,
    staff_id: Optional[UUID] = None,
) -> Stamp:
    """Issue a stamp. Enforces the per-shop scan cooldown.

    `method`: customer_scan | shop_scan | phone_entry | system.
    `system` bypasses the cooldown (bonus/birthday/admin stamps).

    Raises IssuanceError on cooldown violation or invalid branch context.
    """
    if method not in VALID_METHODS:
        raise ValueError(f"Invalid issuance method: {method}")

    # In 'separate' reward mode, a branch must be chosen (we don't know which card to stamp otherwise).
    if shop.reward_mode == "separate" and branch_id is None:
        raise IssuanceError("branch_id is required when shop is in 'separate' reward mode")

    cooldown = shop.scan_cooldown_minutes or 0
    if method != "system" and cooldown > 0:
        threshold = utcnow() - timedelta(minutes=cooldown)
        if await _has_recent_stamp(db, shop.id, customer.id, threshold):
            raise IssuanceError(
                f"Scan cooldown not yet elapsed ({cooldown} min)"
            )

    stamp = Stamp(
        shop_id=shop.id,
        customer_id=customer.id,
        branch_id=branch_id,
        issuance_method=method,
        issued_by_staff_id=staff_id,
    )
    db.add(stamp)
    await db.commit()
    await db.refresh(stamp)
    return stamp


async def void_stamp(
    db: AsyncSession,
    stamp: Stamp,
    *,
    by_staff_id: Optional[UUID] = None,
) -> Stamp:
    """Void a stamp. The 60-second window check is enforced by the caller (route layer)."""
    stamp.is_voided = True
    stamp.voided_at = utcnow()
    stamp.voided_by_staff_id = by_staff_id
    db.add(stamp)
    await db.commit()
    await db.refresh(stamp)
    return stamp
