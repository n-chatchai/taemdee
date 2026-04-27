"""Suggestion-engine tests. We seed stamps directly to bypass the daily cap
and dial the timestamps to land each customer in the right bucket."""

from datetime import timedelta

from app.models import Customer, Point
from app.models.util import utcnow
from app.services.deereach import (
    compute_suggestions,
    find_almost_there_customers,
    find_lapsed_customers,
    find_new_customers,
    find_unredeemed_reward_customers,
)


async def _customer(db, *, line_id: str | None = None, phone: str | None = None) -> Customer:
    c = Customer(is_anonymous=line_id is None and phone is None, line_id=line_id, phone=phone)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _stamp(db, shop, customer, *, days_ago: int):
    s = Point(
        shop_id=shop.id,
        customer_id=customer.id,
        issuance_method="customer_scan",
        created_at=utcnow() - timedelta(days=days_ago),
    )
    db.add(s)
    await db.commit()
    return s


async def test_lapsed_returns_only_in_window(db, shop):
    in_window = await _customer(db, line_id="U_in_window")
    too_recent = await _customer(db, line_id="U_too_recent")
    too_old = await _customer(db, line_id="U_too_old")

    await _stamp(db, shop, in_window, days_ago=60)    # 14..365 → match
    await _stamp(db, shop, too_recent, days_ago=7)    # under 14 → skip
    await _stamp(db, shop, too_old, days_ago=400)     # over 365 → skip

    lapsed = await find_lapsed_customers(db, shop)
    assert {c.line_id for c in lapsed} == {"U_in_window"}


async def test_lapsed_skips_anonymous_customers(db, shop):
    """Anonymous customers (no LINE id, no phone) can't be reached."""
    anon = await _customer(db)
    await _stamp(db, shop, anon, days_ago=45)

    lapsed = await find_lapsed_customers(db, shop)
    assert lapsed == []


async def test_almost_there_filters_by_active_count(db, shop):
    """reward_threshold=10. gap_max=2 → match when active count is 8 or 9."""
    almost = await _customer(db, line_id="U_almost")
    far = await _customer(db, line_id="U_far")

    # U_almost: 9 stamps, last 1 day ago → match
    for i in range(9):
        await _stamp(db, shop, almost, days_ago=1)
    # U_far: 4 stamps → skip
    for i in range(4):
        await _stamp(db, shop, far, days_ago=1)

    matches = await find_almost_there_customers(db, shop)
    assert {c.line_id for c in matches} == {"U_almost"}


async def test_unredeemed_reward(db, shop):
    """At goal (10/10), no redemption, last stamp ≥7 days ago → match."""
    forgot = await _customer(db, line_id="U_forgot")
    just_earned = await _customer(db, line_id="U_just_earned")

    # U_forgot: 10 stamps, latest 14 days ago → match
    for i in range(10):
        await _stamp(db, shop, forgot, days_ago=14)
    # U_just_earned: 10 stamps, latest 1 day ago → too soon to nudge
    for i in range(10):
        await _stamp(db, shop, just_earned, days_ago=1)

    matches = await find_unredeemed_reward_customers(db, shop)
    assert {c.line_id for c in matches} == {"U_forgot"}


async def test_new_customer_first_stamp_within_window(db, shop):
    """First stamp at this shop must be within the last 7 days. A customer
    whose earliest stamp here is older than 7 days isn't 'new' anymore — even
    if they kept stamping today."""
    fresh = await _customer(db, line_id="U_fresh")
    not_so_fresh = await _customer(db, line_id="U_not_so_fresh")

    # U_fresh: first (and only) stamp 2 days ago → match
    await _stamp(db, shop, fresh, days_ago=2)
    # U_not_so_fresh: oldest stamp 30 days ago, latest stamp today → SKIP
    await _stamp(db, shop, not_so_fresh, days_ago=30)
    await _stamp(db, shop, not_so_fresh, days_ago=0)

    matches = await find_new_customers(db, shop)
    assert {c.line_id for c in matches} == {"U_fresh"}


async def test_new_customer_skips_no_line_id(db, shop):
    """No LINE id = unreachable, so don't surface them."""
    anon = await _customer(db)
    await _stamp(db, shop, anon, days_ago=1)
    assert await find_new_customers(db, shop) == []


async def test_compute_suggestions_orders_by_urgency(db, shop):
    """Unredeemed-reward → almost-there → win-back → new-customer.

    The almost-there customer's stamps all land within the new-customer
    7-day window, so the same person counts for both kinds — that's
    expected (audiences can overlap; the shop owner picks).
    """
    # Seed one customer in each bucket — forgot's stamps land at day=10
    # (idle long enough for unredeemed_reward, but newer than the 14-day
    # win_back cutoff, so the kinds don't overlap on this customer).
    forgot = await _customer(db, line_id="U_forgot")
    for _ in range(10):
        await _stamp(db, shop, forgot, days_ago=10)

    almost = await _customer(db, line_id="U_almost")
    for _ in range(9):
        await _stamp(db, shop, almost, days_ago=1)

    lapsed = await _customer(db, line_id="U_lapsed")
    await _stamp(db, shop, lapsed, days_ago=60)

    suggestions = await compute_suggestions(db, shop)
    assert [s.kind for s in suggestions] == [
        "unredeemed_reward", "almost_there", "win_back", "new_customer"
    ]
    for s in suggestions:
        assert s.audience_count == 1
        assert s.cost_credit == 1  # 1 LINE message per recipient


async def test_compute_suggestions_empty_when_no_eligible(db, shop):
    suggestions = await compute_suggestions(db, shop)
    assert suggestions == []


async def test_send_campaign_deducts_credits_and_records(db, shop):
    """Send: charges credits, creates a Campaign row, logs a CreditLog entry."""
    from app.models import CreditLog, DeeReachCampaign
    from app.services.deereach import send_campaign
    from sqlmodel import select

    forgot = await _customer(db, line_id="U_forgot")
    for _ in range(10):
        await _stamp(db, shop, forgot, days_ago=14)

    shop.credit_balance = 50
    db.add(shop)
    await db.commit()

    campaign = await send_campaign(db, shop, "unredeemed_reward")
    assert campaign.kind == "unredeemed_reward"
    assert campaign.audience_count == 1
    assert campaign.credits_spent == 1
    assert campaign.sent_at is not None

    await db.refresh(shop)
    assert shop.credit_balance == 49

    # CreditLog entry exists with the deduction
    log_rows = (await db.exec(
        select(CreditLog).where(CreditLog.shop_id == shop.id, CreditLog.reason == "deereach_send")
    )).all()
    assert len(log_rows) == 1
    assert log_rows[0].amount == -1


async def test_send_campaign_empty_audience_raises(db, shop):
    from app.services.deereach import DeeReachSendError, send_campaign

    import pytest
    with pytest.raises(DeeReachSendError, match="ไม่มีผู้รับ"):
        await send_campaign(db, shop, "win_back")


async def test_send_campaign_insufficient_credits_raises(db, shop):
    from app.services.deereach import DeeReachSendError, send_campaign

    forgot = await _customer(db, line_id="U_forgot")
    for _ in range(10):
        await _stamp(db, shop, forgot, days_ago=14)

    shop.credit_balance = 0
    db.add(shop)
    await db.commit()

    import pytest
    with pytest.raises(DeeReachSendError, match="เครดิตไม่พอ"):
        await send_campaign(db, shop, "unredeemed_reward")


async def test_compute_suggestions_caps_at_max(db, shop):
    forgot = await _customer(db, line_id="U_forgot")
    for _ in range(10):
        await _stamp(db, shop, forgot, days_ago=14)
    almost = await _customer(db, line_id="U_almost")
    for _ in range(9):
        await _stamp(db, shop, almost, days_ago=1)
    lapsed = await _customer(db, line_id="U_lapsed")
    await _stamp(db, shop, lapsed, days_ago=60)

    capped = await compute_suggestions(db, shop, max_suggestions=2)
    assert len(capped) == 2
