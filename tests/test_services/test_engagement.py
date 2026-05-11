"""Unit tests for the customer engagement score.

Covers the per-kind weights, the time window, the tier mapping, and
the bulk helper used by the /shop/customers list."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.models import DeeReachEvent
from app.models.util import utcnow
from app.services.engagement import (
    TIER_COLD,
    TIER_HOT,
    TIER_WARM,
    WINDOW_DAYS,
    engagement_score,
    engagement_scores_bulk,
    engagement_tier,
    tier_label,
)


def _seed_event(db, *, inbox_id, customer_id, shop_id, kind, days_ago=0):
    """Helper — log a raw DeeReachEvent at a specific age. Bypasses
    the log_event service so tests can dial the timestamp directly."""
    e = DeeReachEvent(
        inbox_id=inbox_id,
        customer_id=customer_id,
        shop_id=shop_id,
        campaign_id=None,
        kind=kind,
        created_at=utcnow() - timedelta(days=days_ago),
    )
    db.add(e)
    return e


# ── tier mapping ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("score,expected", [
    (0, TIER_COLD),
    (-3, TIER_COLD),  # defensive — negative shouldn't happen but maps cold
    (1, TIER_WARM),
    (4, TIER_WARM),
    (5, TIER_HOT),
    (50, TIER_HOT),
])
def test_engagement_tier_mapping(score, expected):
    assert engagement_tier(score) == expected


def test_tier_label_returns_thai_string():
    assert tier_label(TIER_HOT) == "ขาประจำส่ง"
    assert tier_label(TIER_WARM) == "เริ่มสนใจ"
    assert tier_label(TIER_COLD) == "เงียบ"


# ── scoring ────────────────────────────────────────────────────────────────


async def test_score_zero_for_customer_with_no_events(db, shop, customer):
    s = await engagement_score(db, shop_id=shop.id, customer_id=customer.id)
    assert s == 0


async def test_score_weights_per_kind(db, shop, customer, inbox_row):
    """opened=1, replied=3, voucher_claimed=5. 1+3+5 = 9 → hot."""
    _seed_event(db, inbox_id=inbox_row.id, customer_id=customer.id, shop_id=shop.id, kind="opened")
    _seed_event(db, inbox_id=inbox_row.id, customer_id=customer.id, shop_id=shop.id, kind="replied")
    _seed_event(db, inbox_id=inbox_row.id, customer_id=customer.id, shop_id=shop.id, kind="voucher_claimed")
    await db.commit()

    s = await engagement_score(db, shop_id=shop.id, customer_id=customer.id)
    assert s == 9
    assert engagement_tier(s) == TIER_HOT


async def test_score_ignores_unknown_kinds(db, shop, customer, inbox_row):
    """Future kinds added to the DB but not yet in _POINTS contribute
    zero — the score stays stable across migrations."""
    _seed_event(db, inbox_id=inbox_row.id, customer_id=customer.id, shop_id=shop.id, kind="opened")
    _seed_event(db, inbox_id=inbox_row.id, customer_id=customer.id, shop_id=shop.id, kind="story_visited")
    await db.commit()

    s = await engagement_score(db, shop_id=shop.id, customer_id=customer.id)
    assert s == 1  # only opened contributed


async def test_score_excludes_events_outside_window(db, shop, customer, inbox_row):
    """Events older than WINDOW_DAYS don't count toward the live score."""
    # one in window, one safely outside
    _seed_event(db, inbox_id=inbox_row.id, customer_id=customer.id, shop_id=shop.id, kind="replied", days_ago=10)
    _seed_event(db, inbox_id=inbox_row.id, customer_id=customer.id, shop_id=shop.id, kind="replied", days_ago=WINDOW_DAYS + 5)
    await db.commit()

    s = await engagement_score(db, shop_id=shop.id, customer_id=customer.id)
    assert s == 3


async def test_score_scoped_per_shop(db, shop, customer, inbox_row):
    """A customer's engagement at one shop must not bleed into
    another shop's score for the same customer."""
    from app.models import Shop
    other = Shop(name="Other", phone="0899999998", reward_threshold=5)
    db.add(other)
    await db.commit()
    await db.refresh(other)

    _seed_event(db, inbox_id=inbox_row.id, customer_id=customer.id, shop_id=shop.id, kind="opened")
    # Event "belongs" to a different shop — even though inbox_id is
    # reused, the score query filters on shop_id.
    _seed_event(db, inbox_id=inbox_row.id, customer_id=customer.id, shop_id=other.id, kind="replied")
    await db.commit()

    assert await engagement_score(db, shop_id=shop.id, customer_id=customer.id) == 1
    assert await engagement_score(db, shop_id=other.id, customer_id=customer.id) == 3


# ── bulk variant ───────────────────────────────────────────────────────────


async def test_bulk_returns_zero_for_unknown_customers(db, shop):
    out = await engagement_scores_bulk(
        db, shop_id=shop.id, customer_ids=[uuid4(), uuid4()],
    )
    assert all(v == 0 for v in out.values())


async def test_bulk_handles_empty_input(db, shop):
    assert await engagement_scores_bulk(db, shop_id=shop.id, customer_ids=[]) == {}


async def test_bulk_aggregates_per_customer(db, shop, customer, inbox_row):
    """Two events for one customer = summed score, multiple customers
    each get their own bucket."""
    from tests._helpers import make_customer
    c2 = await make_customer(db, line_id="U_other")

    _seed_event(db, inbox_id=inbox_row.id, customer_id=customer.id, shop_id=shop.id, kind="opened")
    _seed_event(db, inbox_id=inbox_row.id, customer_id=customer.id, shop_id=shop.id, kind="replied")
    _seed_event(db, inbox_id=inbox_row.id, customer_id=c2.id, shop_id=shop.id, kind="opened")
    await db.commit()

    scores = await engagement_scores_bulk(
        db, shop_id=shop.id, customer_ids=[customer.id, c2.id],
    )
    assert scores[customer.id] == 4  # 1 + 3
    assert scores[c2.id] == 1
