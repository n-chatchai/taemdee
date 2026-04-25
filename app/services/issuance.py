"""Stamp issuance — the core act across all 3 methods, plus void.

Anti-fraud in v1 is the daily cap (1 stamp per customer per shop per day by default).
Per-device cooldown + geofence + pattern alerts are deferred to v1.5 / v2.
"""

from typing import Optional
from uuid import UUID

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Customer, Shop, Stamp
from app.models.util import utcnow

VALID_METHODS = {"customer_scan", "shop_scan", "phone_entry", "system"}

DEFAULT_DAILY_CAP = 1  # Max stamps per customer per shop per day (per PRD §5.C)


class IssuanceError(Exception):
    pass


async def _stamps_today_count(
    db: AsyncSession, shop_id: UUID, customer_id: UUID
) -> int:
    today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.exec(
        select(func.count())
        .select_from(Stamp)
        .where(
            Stamp.shop_id == shop_id,
            Stamp.customer_id == customer_id,
            Stamp.created_at >= today_start,
            Stamp.is_voided == False,  # noqa: E712
        )
    )
    return result.one()


async def issue_stamp(
    db: AsyncSession,
    shop: Shop,
    customer: Customer,
    method: str,
    *,
    branch_id: Optional[UUID] = None,
    staff_id: Optional[UUID] = None,
) -> Stamp:
    """Issue a stamp. Enforces the daily cap (per-customer per-shop).

    `method`: customer_scan | shop_scan | phone_entry | system.
    `system` bypasses the daily cap (bonus/birthday/admin stamps).

    Raises IssuanceError on cap violation or invalid branch context.
    """
    if method not in VALID_METHODS:
        raise ValueError(f"Invalid issuance method: {method}")

    # In 'separate' reward mode, a branch must be chosen (we don't know which card to stamp otherwise).
    if shop.reward_mode == "separate" and branch_id is None:
        raise IssuanceError("branch_id is required when shop is in 'separate' reward mode")

    if method != "system":
        count_today = await _stamps_today_count(db, shop.id, customer.id)
        if count_today >= DEFAULT_DAILY_CAP:
            raise IssuanceError(
                f"Daily cap reached for this customer at this shop "
                f"({DEFAULT_DAILY_CAP}/day)"
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
