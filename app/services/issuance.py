"""Point issuance — the core act across all 3 methods, plus void.

Anti-rescan policy is per-shop: `Shop.scan_cooldown_minutes` (default 0 = no
cooldown). When > 0, a customer must wait that many minutes between stamps at
the same shop. Geofence + pattern alerts are deferred.
"""

import logging
from datetime import timedelta
from typing import Optional, Tuple
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Customer, Redemption, Shop, Point
from app.models.util import utcnow

log = logging.getLogger(__name__)

VALID_METHODS = {"customer_scan", "shop_scan", "phone_entry", "system"}


class IssuanceError(Exception):
    pass


async def _has_recent_stamp(
    db: AsyncSession, shop_id: UUID, customer_id: UUID, since,
) -> bool:
    """True if a non-voided stamp exists for (shop, customer) at or after `since`."""
    result = await db.exec(
        select(Point.id)
        .where(
            Point.shop_id == shop_id,
            Point.customer_id == customer_id,
            Point.created_at >= since,
            Point.is_voided == False,  # noqa: E712
        )
        .limit(1)
    )
    return result.first() is not None


async def issue_point(
    db: AsyncSession,
    shop: Shop,
    customer: Customer,
    method: str,
    *,
    branch_id: Optional[UUID] = None,
    staff_id: Optional[UUID] = None,
) -> Tuple[Point, Optional[Redemption]]:
    """Issue a stamp; auto-redeem if it brings the customer to threshold.

    `method`: customer_scan | shop_scan | phone_entry | system.
    `system` bypasses the cooldown (bonus/birthday/admin stamps).

    Returns `(stamp, redemption)`. `redemption` is None unless this stamp
    pushed the customer's active count to ≥ `shop.reward_threshold` and the
    inline `redeem()` call succeeded; if the redemption attempt hit a
    RedemptionError (e.g. a transient race), the stamp is still committed
    and the caller gets None — the next issuance attempt will retry.

    Auto-redeem lives here (not in routes) so every issuance entry point —
    customer scan, shop-side scan, phone entry, manual insert, DeeReach
    bonus_stamp_count grants — is covered by a single source of truth. A
    new caller can't accidentally skip the threshold check.

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

    stamp = Point(
        shop_id=shop.id,
        customer_id=customer.id,
        branch_id=branch_id,
        issuance_method=method,
        issued_by_staff_id=staff_id,
    )
    db.add(stamp)
    await db.commit()
    await db.refresh(stamp)

    redemption = await _maybe_auto_redeem(db, shop, customer, branch_id)
    return stamp, redemption


async def _maybe_auto_redeem(
    db: AsyncSession,
    shop: Shop,
    customer: Customer,
    branch_id: Optional[UUID],
) -> Optional[Redemption]:
    """Fire `redeem()` if the customer just crossed the threshold. Returns
    None when there's nothing to redeem yet, or when the inline redeem
    failed (logged for visibility — the next issuance retries)."""
    # Local import to avoid a circular at module load time
    # (services/redemption may import issuance helpers in the future).
    from app.services.redemption import RedemptionError, active_point_count, redeem

    scope_branch = branch_id if shop.reward_mode == "separate" else None
    count = await active_point_count(db, shop.id, customer.id, scope_branch)
    if count < shop.reward_threshold:
        return None
    try:
        return await redeem(
            db, shop, customer,
            branch_id=branch_id if shop.reward_mode == "separate" else None,
        )
    except RedemptionError as e:
        log.warning(
            "auto-redeem failed for shop=%s customer=%s: %s",
            shop.id, customer.id, e,
        )
        return None


async def void_point(
    db: AsyncSession,
    stamp: Point,
    *,
    by_staff_id: Optional[UUID] = None,
) -> Point:
    """Void a stamp."""
    stamp.is_voided = True
    stamp.voided_at = utcnow()
    stamp.voided_by_staff_id = by_staff_id
    db.add(stamp)
    await db.commit()
    await db.refresh(stamp)
    return stamp
