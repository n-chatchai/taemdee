"""Reward redemption — consume stamps, create a Redemption record, support 60-sec void.

A stamp is "active" (available for a new redemption) when:
    - it is not voided (`is_voided = false`), AND
    - its `redemption_id` is NULL, OR the referenced Redemption was voided.

Voiding a Redemption flips its `is_voided` flag — the attached stamps become available
again for a fresh redemption.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import and_, or_
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Customer, Redemption, Shop, Point
from app.models.util import utcnow


class RedemptionError(Exception):
    pass


def _active_stamp_where(
    shop_id: UUID, customer_id: UUID, branch_id: Optional[UUID]
):
    conds = [
        Point.shop_id == shop_id,
        Point.customer_id == customer_id,
        Point.is_voided == False,  # noqa: E712
        or_(
            Point.redemption_id.is_(None),
            Point.redemption_id.in_(
                select(Redemption.id).where(Redemption.is_voided == True)  # noqa: E712
            ),
        ),
    ]
    if branch_id is not None:
        conds.append(Point.branch_id == branch_id)
    return and_(*conds)


async def active_point_count(
    db: AsyncSession,
    shop_id: UUID,
    customer_id: UUID,
    branch_id: Optional[UUID] = None,
) -> int:
    """Number of stamps available for a new redemption.

    In 'separate' reward mode the caller must pass branch_id; in 'shared' mode pass None.
    """
    result = await db.exec(
        select(func.count())
        .select_from(Point)
        .where(_active_stamp_where(shop_id, customer_id, branch_id))
    )
    return result.one()


async def redeem(
    db: AsyncSession,
    shop: Shop,
    customer: Customer,
    branch_id: Optional[UUID] = None,
) -> Redemption:
    """Claim the reward. Consumes `shop.reward_threshold` active stamps (oldest first).

    In 'separate' mode, `branch_id` must be provided and stamps are consumed from that
    branch only. In 'shared' mode, `branch_id` (if given) is recorded as where the claim
    happened, but the consumed stamps come from any branch.

    Raises RedemptionError if the customer doesn't have enough active stamps.
    """
    if shop.reward_mode == "separate" and branch_id is None:
        raise RedemptionError(
            "branch_id is required when shop is in 'separate' reward mode"
        )

    scope_branch = branch_id if shop.reward_mode == "separate" else None

    count = await active_point_count(db, shop.id, customer.id, scope_branch)
    if count < shop.reward_threshold:
        raise RedemptionError(
            f"Not enough stamps to redeem: have {count}, need {shop.reward_threshold}"
        )

    # Pull the oldest active stamps to consume
    result = await db.exec(
        select(Point)
        .where(_active_stamp_where(shop.id, customer.id, scope_branch))
        .order_by(Point.created_at)
        .limit(shop.reward_threshold)
    )
    stamps_to_consume = list(result.all())

    redemption = Redemption(
        shop_id=shop.id,
        customer_id=customer.id,
        branch_id=branch_id,
    )
    db.add(redemption)
    await db.flush()  # populate redemption.id

    for stamp in stamps_to_consume:
        stamp.redemption_id = redemption.id
        db.add(stamp)

    await db.commit()
    await db.refresh(redemption)
    return redemption


async def void_redemption(
    db: AsyncSession,
    redemption: Redemption,
    *,
    by_staff_id: Optional[UUID] = None,
) -> Redemption:
    """Void a redemption. The attached stamps become available again.

    The 60-second window check is enforced by the caller (route layer).
    """
    redemption.is_voided = True
    redemption.voided_at = utcnow()
    redemption.voided_by_staff_id = by_staff_id
    db.add(redemption)
    await db.commit()
    await db.refresh(redemption)
    return redemption
