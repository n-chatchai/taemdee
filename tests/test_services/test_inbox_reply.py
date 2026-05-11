"""Unit tests for the broadcast-scoped reply service.

Covers send_reply on both sides (rate limit on customer, read_at reset
on shop), the read-stamping helpers, the unread totals, and the
author-anchored markup shape emitted by _comment_html."""

from datetime import timedelta

import pytest

from app.models import Customer, Inbox, InboxReply
from app.models.util import utcnow
from app.services.inbox_reply import (
    CUSTOMER_RATE_LIMIT,
    RateLimited,
    _comment_html,
    customer_unread_total,
    list_replies,
    mark_shop_read,
    send_reply,
    shop_unread_inbox_ids,
    shop_unread_total,
)


async def test_list_replies_empty(db, inbox_row):
    assert await list_replies(db, inbox_row.id) == []


async def test_list_replies_oldest_first(db, inbox_row):
    """Detail page renders top-to-bottom in chronological order."""
    now = utcnow()
    a = InboxReply(inbox_id=inbox_row.id, sender="customer", body="ก่อน",
                   created_at=now - timedelta(minutes=2))
    b = InboxReply(inbox_id=inbox_row.id, sender="shop", body="ตรงกลาง",
                   created_at=now - timedelta(minutes=1))
    c = InboxReply(inbox_id=inbox_row.id, sender="customer", body="หลังสุด",
                   created_at=now)
    db.add_all([c, a, b])  # insert out of order; sort must come from service
    await db.commit()

    rows = await list_replies(db, inbox_row.id)
    assert [r.body for r in rows] == ["ก่อน", "ตรงกลาง", "หลังสุด"]


async def test_send_reply_customer_persists_and_publishes_to_shop(
    db, inbox_row, stub_events_publish
):
    reply = await send_reply(db, inbox_row, sender="customer", body="โอเคค่ะ")
    assert reply.id is not None
    assert reply.sender == "customer"
    assert reply.body == "โอเคค่ะ"

    # SSE: shop's open detail page gets the new comment + dock counter bump.
    targets = [t for t in stub_events_publish if t[0] == "shop"]
    names = [t[2] for t in targets]
    assert "inbox-reply-in" in names
    assert "messages-update" in names
    shop_id = str(inbox_row.shop_id)
    assert all(t[1] == shop_id for t in targets)


async def test_send_reply_customer_does_not_touch_inbox_read_at(
    db, inbox_row, stub_events_publish
):
    """Only the shop's reply should reset Inbox.read_at — a customer
    follow-up shouldn't ping their own dock."""
    inbox_row.read_at = utcnow()
    db.add(inbox_row)
    await db.commit()

    await send_reply(db, inbox_row, sender="customer", body="x")
    await db.refresh(inbox_row)
    assert inbox_row.read_at is not None


async def test_send_reply_shop_resets_inbox_read_at(
    db, inbox_row, stub_events_publish
):
    """Shop reply = new content for the customer → broadcast goes back
    to unread on the customer's inbox list."""
    inbox_row.read_at = utcnow()
    db.add(inbox_row)
    await db.commit()

    reply = await send_reply(db, inbox_row, sender="shop", body="สวัสดีค่ะพี่")
    assert reply.sender == "shop"
    await db.refresh(inbox_row)
    assert inbox_row.read_at is None

    customer_evts = [t for t in stub_events_publish if t[0] == "customer"]
    names = [t[2] for t in customer_evts]
    assert "inbox-reply-in" in names
    assert "inbox-update" in names


async def test_send_reply_customer_rate_limit(
    db, inbox_row, stub_events_publish
):
    """3 customer replies on the same broadcast within the window is
    fine; the 4th raises RateLimited."""
    for i in range(CUSTOMER_RATE_LIMIT):
        await send_reply(db, inbox_row, sender="customer", body=f"r{i}")

    with pytest.raises(RateLimited):
        await send_reply(db, inbox_row, sender="customer", body="overflow")


async def test_send_reply_shop_unrestricted(db, inbox_row, stub_events_publish):
    """Shop side doesn't share the customer rate limit — operator
    replies are always desired."""
    for i in range(CUSTOMER_RATE_LIMIT + 2):
        await send_reply(db, inbox_row, sender="shop", body=f"s{i}")

    rows = await list_replies(db, inbox_row.id)
    assert len(rows) == CUSTOMER_RATE_LIMIT + 2


async def test_send_reply_empty_body_raises(db, inbox_row):
    with pytest.raises(ValueError):
        await send_reply(db, inbox_row, sender="customer", body="   ")


async def test_send_reply_invalid_sender_raises(db, inbox_row):
    with pytest.raises(ValueError):
        await send_reply(db, inbox_row, sender="bot", body="hi")


async def test_mark_shop_read_stamps_only_customer_replies(
    db, inbox_row, stub_events_publish
):
    await send_reply(db, inbox_row, sender="customer", body="ลูกค้า 1")
    await send_reply(db, inbox_row, sender="shop", body="ร้านตอบ")
    await send_reply(db, inbox_row, sender="customer", body="ลูกค้า 2")

    n = await mark_shop_read(db, inbox_row)
    assert n == 2  # only the two customer rows

    rows = await list_replies(db, inbox_row.id)
    for r in rows:
        if r.sender == "customer":
            assert r.shop_read_at is not None
        else:
            assert r.shop_read_at is None


async def test_mark_shop_read_idempotent(db, inbox_row, stub_events_publish):
    """Second open of the detail page should report zero new stamps."""
    await send_reply(db, inbox_row, sender="customer", body="hi")
    assert await mark_shop_read(db, inbox_row) == 1
    assert await mark_shop_read(db, inbox_row) == 0


async def test_shop_unread_total_counts_only_unstamped_customer_replies(
    db, shop, inbox_row, stub_events_publish
):
    await send_reply(db, inbox_row, sender="customer", body="a")
    await send_reply(db, inbox_row, sender="customer", body="b")
    await send_reply(db, inbox_row, sender="shop", body="c")  # shop doesn't count

    assert await shop_unread_total(db, shop.id) == 2

    await mark_shop_read(db, inbox_row)
    assert await shop_unread_total(db, shop.id) == 0


async def test_shop_unread_inbox_ids_groups_per_inbox(
    db, shop, customer, inbox_row, stub_events_publish
):
    """Two unread replies on the same inbox = one inbox id in the
    group-by result (not two)."""
    await send_reply(db, inbox_row, sender="customer", body="a")
    await send_reply(db, inbox_row, sender="customer", body="b")

    # Second inbox with no replies — should NOT appear in the list.
    quiet = Inbox(customer_id=customer.id, shop_id=shop.id, body="ping")
    db.add(quiet)
    await db.commit()
    await db.refresh(quiet)

    ids = await shop_unread_inbox_ids(db, shop.id)
    assert ids == [inbox_row.id]


async def test_customer_unread_total_counts_unread_inboxes(
    db, customer, shop
):
    """customer_unread_total scans Inbox.read_at IS NULL — direct read
    of the inbox list, not the reply table."""
    a = Inbox(customer_id=customer.id, shop_id=shop.id, body="1")
    b = Inbox(customer_id=customer.id, shop_id=shop.id, body="2", read_at=utcnow())
    c = Inbox(customer_id=customer.id, shop_id=shop.id, body="3")
    db.add_all([a, b, c])
    await db.commit()

    assert await customer_unread_total(db, customer.id) == 2


async def test_customer_unread_bumps_when_shop_replies(
    db, customer, inbox_row, stub_events_publish
):
    """A shop reply on an already-read broadcast pulls it back into the
    unread count — same path the dock badge reads."""
    inbox_row.read_at = utcnow()
    db.add(inbox_row)
    await db.commit()
    assert await customer_unread_total(db, customer.id) == 0

    await send_reply(db, inbox_row, sender="shop", body="ตามมา")
    assert await customer_unread_total(db, customer.id) == 1


def _make_reply(*, sender: str, body: str = "สวัสดี") -> InboxReply:
    """Build an InboxReply without a DB roundtrip — _comment_html only
    needs id / sender / body / created_at populated."""
    from uuid import uuid4
    return InboxReply(
        id=uuid4(),
        inbox_id=uuid4(),
        sender=sender,
        body=body,
        created_at=utcnow(),
    )


def test_comment_html_customer_shape():
    r = _make_reply(sender="customer", body="ขอบคุณค่ะ")
    out = _comment_html(
        r, customer_label="พี่นิ", customer_initial="พ",
        shop_label="คาเฟ่", shop_initial="ค",
    )
    assert 'class="ix-comment"' in out  # no .shop modifier
    assert f'data-reply-id="{r.id}"' in out
    assert '<div class="ixc-avatar">พ</div>' in out
    assert '<span class="ixc-author">พี่นิ</span>' in out
    assert '<div class="ixc-text">ขอบคุณค่ะ</div>' in out


def test_comment_html_shop_shape_has_shop_class():
    r = _make_reply(sender="shop", body="ยินดีค่ะ")
    out = _comment_html(
        r, customer_label="พี่นิ", customer_initial="พ",
        shop_label="คาเฟ่", shop_initial="ค",
    )
    assert 'class="ix-comment shop"' in out
    assert '<div class="ixc-avatar">ค</div>' in out
    assert '<span class="ixc-author">คาเฟ่</span>' in out


def test_comment_html_escapes_html_in_body():
    """Reply bodies go through escape — no raw markup in the SSE payload."""
    r = _make_reply(sender="customer", body='<script>alert(1)</script>')
    out = _comment_html(
        r, customer_label="x", customer_initial="x",
        shop_label="y", shop_initial="y",
    )
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
