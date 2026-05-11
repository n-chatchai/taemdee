"""Customer engagement score computed from the DeeReachEvent log.

Phase 3 of the engagement track. Surfaces a per-customer score on
/shop/customers so the operator can see at a glance which customers
are actively engaging with broadcasts vs which are silent.

Scoring rules (kept simple + explainable — the operator needs to be
able to reason about why a customer scored X):

  Per event in the last `WINDOW_DAYS` days, scoped to the shop:
    · opened          → 1 point
    · replied         → 3 points  (active engagement, two-way signal)
    · voucher_claimed → 5 points  (highest intent — they acted on the offer)

  Tier from total score:
    0      → 'cold'   (no engagement in window)
    1..4   → 'warm'   (opened a few broadcasts)
    5+     → 'hot'    (replied / claimed)

The score is computed live — no stored snapshot. The events table
indexes (customer_id, kind) so per-customer queries are cheap.
"""

from datetime import datetime, timedelta
from typing import Iterable
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import DeeReachEvent
from app.models.util import utcnow


WINDOW_DAYS = 90

# Per-kind point values — keep in sync with the docstring above.
_POINTS = {
    "opened": 1,
    "replied": 3,
    "voucher_claimed": 5,
}

TIER_COLD = "cold"
TIER_WARM = "warm"
TIER_HOT = "hot"


def engagement_tier(score: int) -> str:
    """Map a raw score to a 3-band tier label."""
    if score <= 0:
        return TIER_COLD
    if score < 5:
        return TIER_WARM
    return TIER_HOT


async def engagement_score(
    db: AsyncSession,
    *,
    shop_id: UUID,
    customer_id: UUID,
) -> int:
    """Sum of weighted events in the trailing `WINDOW_DAYS` window for
    one (shop, customer) pair. Unknown event kinds count as zero so
    adding a new kind later is a no-op for the score until the
    _POINTS map is updated."""
    cutoff = utcnow() - timedelta(days=WINDOW_DAYS)
    rows = (await db.exec(
        select(DeeReachEvent).where(
            DeeReachEvent.shop_id == shop_id,
            DeeReachEvent.customer_id == customer_id,
            DeeReachEvent.created_at >= cutoff,
        )
    )).all()
    return sum(_POINTS.get(e.kind, 0) for e in rows)


async def engagement_scores_bulk(
    db: AsyncSession,
    *,
    shop_id: UUID,
    customer_ids: Iterable[UUID],
) -> dict[UUID, int]:
    """Bulk variant for the /shop/customers list — one query for all
    customers instead of N. Customers with zero events default to 0
    (no row in the return is the same as the score being 0)."""
    ids = list(customer_ids)
    if not ids:
        return {}
    cutoff = utcnow() - timedelta(days=WINDOW_DAYS)
    rows = (await db.exec(
        select(DeeReachEvent).where(
            DeeReachEvent.shop_id == shop_id,
            DeeReachEvent.customer_id.in_(ids),
            DeeReachEvent.created_at >= cutoff,
        )
    )).all()
    scores: dict[UUID, int] = {cid: 0 for cid in ids}
    for e in rows:
        scores[e.customer_id] = scores.get(e.customer_id, 0) + _POINTS.get(e.kind, 0)
    return scores


def tier_label(tier: str) -> str:
    """Thai-facing label for the tier badge."""
    return {
        TIER_HOT: "ขาประจำส่ง",
        TIER_WARM: "เริ่มสนใจ",
        TIER_COLD: "เงียบ",
    }.get(tier, "")
