"""Suggestion-engine tests. We seed stamps directly to bypass the daily cap
and dial the timestamps to land each customer in the right bucket."""

from datetime import timedelta

from sqlmodel import select

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
    """Lock: deducts satang from balance, creates a campaign in 'locked' state
    with locked_credits_satang set, logs a CreditLog entry with reason
    deereach_lock. Per v2 (Lock-Enqueue) the row sits at status='locked'
    until the worker reconciles — final_credits_satang stays 0 here."""
    from app.models import CreditLog, DeeReachCampaign  # noqa: F401
    from app.services.deereach import send_campaign

    forgot = await _customer(db, line_id="U_forgot")
    for _ in range(10):
        await _stamp(db, shop, forgot, days_ago=14)

    # Balance is in satang now (1 credit = 100). 5000 satang = 50 credits.
    shop.credit_balance = 5000
    db.add(shop)
    await db.commit()

    campaign = await send_campaign(db, shop, "unredeemed_reward")
    assert campaign.kind == "unredeemed_reward"
    assert campaign.audience_count == 1
    assert campaign.locked_credits_satang == 100  # 1 LINE recipient × 100 satang
    assert campaign.final_credits_satang == 0    # worker hasn't reconciled yet
    assert campaign.status == "locked"
    assert campaign.sent_at is not None

    await db.refresh(shop)
    assert shop.credit_balance == 4900  # 5000 − 100

    log_rows = (await db.exec(
        select(CreditLog).where(CreditLog.shop_id == shop.id, CreditLog.reason == "deereach_lock")
    )).all()
    assert len(log_rows) == 1
    assert log_rows[0].amount == -100


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


async def test_muted_customer_excluded_from_all_kinds(db, shop):
    """Per PRD §10 — a CustomerShopMute row drops the customer from every
    kind, including manual (which bypasses rate-limit but not opt-out)."""
    from app.models import CustomerShopMute
    from app.services.deereach import _audience_for

    forgot = await _customer(db, line_id="U_forgot")
    for _ in range(10):
        await _stamp(db, shop, forgot, days_ago=14)

    # Without mute → unredeemed_reward and manual both include the customer.
    pre_auto = await _audience_for(db, shop, "unredeemed_reward")
    pre_manual = await _audience_for(db, shop, "manual")
    assert forgot.id in {c.id for c in pre_auto}
    assert forgot.id in {c.id for c in pre_manual}

    db.add(CustomerShopMute(customer_id=forgot.id, shop_id=shop.id))
    await db.commit()

    post_auto = await _audience_for(db, shop, "unredeemed_reward")
    post_manual = await _audience_for(db, shop, "manual")
    assert forgot.id not in {c.id for c in post_auto}
    assert forgot.id not in {c.id for c in post_manual}


async def test_manual_audience_bypasses_rate_limit(db, shop):
    """Auto-fired kinds drop customers in the 14-day cooldown; manual
    (owner-composed) campaigns do not — the owner is making an explicit
    human decision per recipient via the S13.detail editor."""
    from datetime import timedelta
    from app.models import DeeReachCampaign, DeeReachMessage
    from app.services.deereach import _audience_for, find_all_reachable_customers

    forgot = await _customer(db, line_id="U_forgot")
    for _ in range(10):
        await _stamp(db, shop, forgot, days_ago=14)

    # Seed a recent message so unredeemed_reward would suppress this one.
    campaign = DeeReachCampaign(
        shop_id=shop.id, kind="unredeemed_reward",
        audience_count=1, status="completed",
        sent_at=utcnow() - timedelta(days=3),
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    db.add(DeeReachMessage(
        campaign_id=campaign.id, customer_id=forgot.id,
        channel="line", cost_satang=100, status="delivered",
        created_at=utcnow() - timedelta(days=3),
    ))
    await db.commit()

    # Auto kind respects the cooldown.
    auto_audience = await _audience_for(db, shop, "unredeemed_reward")
    assert forgot.id not in {c.id for c in auto_audience}

    # Manual kind does not.
    manual_audience = await _audience_for(db, shop, "manual")
    assert forgot.id in {c.id for c in manual_audience}

    # find_all_reachable_customers itself doesn't filter — _audience_for
    # is the layer where the policy lives.
    raw = await find_all_reachable_customers(db, shop)
    assert forgot.id in {c.id for c in raw}


async def test_send_campaign_uses_message_override(db, shop):
    """Owner can edit the body in S13.detail and that text is what gets
    saved on the campaign + sent to recipients."""
    from app.models import DeeReachCampaign
    from app.services.deereach import send_campaign

    forgot = await _customer(db, line_id="U_forgot")
    for _ in range(10):
        await _stamp(db, shop, forgot, days_ago=14)

    shop.credit_balance = 5000
    db.add(shop)
    await db.commit()

    custom = "เปิดใหม่วันนี้ — ขอใส่กาแฟแก้วโปรดให้พี่ฟรี!"
    campaign = await send_campaign(db, shop, "unredeemed_reward", message_override=custom)
    assert campaign.message_text == custom


async def test_send_campaign_blank_override_rejected(db, shop):
    """Whitespace-only edit must not burn credits — service raises."""
    from app.services.deereach import DeeReachSendError, send_campaign

    forgot = await _customer(db, line_id="U_forgot")
    for _ in range(10):
        await _stamp(db, shop, forgot, days_ago=14)

    shop.credit_balance = 5000
    db.add(shop)
    await db.commit()

    import pytest
    with pytest.raises(DeeReachSendError, match="ข้อความว่าง"):
        await send_campaign(db, shop, "unredeemed_reward", message_override="   \n  ")


async def test_pick_channel_prefers_web_push_over_line(db, shop):
    """Per PRD §10 waterfall — web_push (0.5 Cr) wins when subscribed,
    even though line (1 Cr) is also reachable."""
    from app.services.deereach import _pick_channel

    c = Customer(
        is_anonymous=False,
        line_id="U_both",
        web_push_endpoint="https://example.push/sub-abc",
    )
    assert _pick_channel(c) == "web_push"


async def test_pick_channel_falls_back_to_line_without_web_push(db, shop):
    from app.services.deereach import _pick_channel

    c = Customer(is_anonymous=False, line_id="U_line_only")
    assert _pick_channel(c) == "line"


async def test_pick_channel_falls_back_to_inbox_for_anonymous(db, shop):
    from app.services.deereach import _pick_channel

    c = Customer(is_anonymous=True)
    assert _pick_channel(c) == "inbox"


async def test_compute_suggestions_excludes_recently_messaged(db, shop):
    """Per PRD §10 anti-spam — a customer messaged within the last 14 days
    must drop out of every suggestion's audience until the cooldown clears."""
    from app.models import DeeReachCampaign, DeeReachMessage

    forgot = await _customer(db, line_id="U_forgot")
    for _ in range(10):
        await _stamp(db, shop, forgot, days_ago=14)

    # Without a prior message → unredeemed_reward should fire.
    pre = await compute_suggestions(db, shop)
    assert any(s.kind == "unredeemed_reward" for s in pre)

    # Seed a recent message to this customer (3 days ago) — within the
    # 14-day rate-limit window.
    campaign = DeeReachCampaign(
        shop_id=shop.id, kind="unredeemed_reward",
        audience_count=1, status="completed",
        sent_at=utcnow() - timedelta(days=3),
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    msg = DeeReachMessage(
        campaign_id=campaign.id, customer_id=forgot.id,
        channel="line", cost_satang=100, status="delivered",
        created_at=utcnow() - timedelta(days=3),
    )
    db.add(msg)
    await db.commit()

    post = await compute_suggestions(db, shop)
    assert all(s.kind != "unredeemed_reward" for s in post)


async def test_send_campaign_writes_inbox_for_anonymous_customer(db, shop):
    """Anonymous (no LINE id, no phone, no push) → inbox channel → DB write
    in the inbox table when the dispatcher runs."""
    from app.models import Inbox
    from app.services.deereach import _pick_channel
    from app.tasks.deereach import _send_inbox
    from uuid import uuid4

    anon = await _customer(db)
    assert _pick_channel(anon) == "inbox"

    fake_campaign_id = uuid4()
    delivered = await _send_inbox(
        db, anon.id, shop.id, fake_campaign_id, "ทดสอบกล่องข้อความ",
    )
    assert delivered is True
    await db.commit()

    rows = (await db.exec(select(Inbox).where(Inbox.customer_id == anon.id))).all()
    assert len(rows) == 1
    assert rows[0].body == "ทดสอบกล่องข้อความ"
    assert rows[0].shop_id == shop.id
    assert rows[0].read_at is None


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
