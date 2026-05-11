"""HTTP integration tests for the broadcast-scoped reply flow.

Covers both sides:
  · customer  → /my-inbox/{id} GET + reply POST
  · shop      → /shop/messages, /shop/messages/{id}, reply POST

Uses the conftest `client`, `shop`, `customer`, `inbox_row`, `auth_client`
fixtures + the stub_events_publish autouse fixture so the SSE publish
calls don't try to reach a real Postgres."""

from app.core.auth import CUSTOMER_COOKIE_NAME
from app.models import Customer, InboxReply, User
from app.services.auth import issue_customer_token


def _set_customer_cookie(client, customer_id):
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(customer_id))


# ── Customer side ──────────────────────────────────────────────────────────


async def test_my_inbox_detail_lists_existing_replies(
    client, db, shop, customer, inbox_row
):
    shop.allow_customer_messages = True  # gates the inline reply textarea
    db.add(shop)
    db.add_all([
        InboxReply(inbox_id=inbox_row.id, sender="customer", body="ขอบคุณค่ะ"),
        InboxReply(inbox_id=inbox_row.id, sender="shop", body="ยินดีค่ะ"),
    ])
    await db.commit()

    _set_customer_cookie(client, customer.id)
    r = await client.get(f"/my-inbox/{inbox_row.id}")
    assert r.status_code == 200
    assert "ขอบคุณค่ะ" in r.text
    assert "ยินดีค่ะ" in r.text
    # Inline reply textarea is mounted at the bottom of the thread.
    assert 'name="body"' in r.text


async def test_my_inbox_reply_persists_and_appears_in_detail(
    client, db, shop, customer, inbox_row, stub_events_publish
):
    """Customer reply roundtrip: 303 back to detail, row persisted,
    SSE event fired for the shop side."""
    shop.allow_customer_messages = True
    db.add(shop)
    await db.commit()

    _set_customer_cookie(client, customer.id)
    r = await client.post(
        f"/my-inbox/{inbox_row.id}/reply",
        data={"body": "พรุ่งนี้แวะค่ะ"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/my-inbox/{inbox_row.id}"

    # Body landed on the next render.
    body = (await client.get(f"/my-inbox/{inbox_row.id}")).text
    assert "พรุ่งนี้แวะค่ะ" in body

    # SSE fired toward the shop, not the customer (author-anchored).
    targets = [t for t in stub_events_publish if t[0] == "shop"]
    assert any(t[2] == "inbox-reply-in" for t in targets)


async def test_my_inbox_reply_silently_drops_when_messages_disabled(
    client, db, shop, customer, inbox_row, stub_events_publish
):
    """Shop has opted out of inbound replies → POST 303s back to detail
    without creating an InboxReply row."""
    shop.allow_customer_messages = False
    db.add(shop)
    await db.commit()

    _set_customer_cookie(client, customer.id)
    r = await client.post(
        f"/my-inbox/{inbox_row.id}/reply",
        data={"body": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    from sqlmodel import select
    rows = (await db.exec(
        select(InboxReply).where(InboxReply.inbox_id == inbox_row.id)
    )).all()
    assert rows == []


async def test_my_inbox_reply_empty_body_is_noop(
    client, db, shop, customer, inbox_row
):
    shop.allow_customer_messages = True
    db.add(shop)
    await db.commit()

    _set_customer_cookie(client, customer.id)
    r = await client.post(
        f"/my-inbox/{inbox_row.id}/reply",
        data={"body": "   "},
        follow_redirects=False,
    )
    assert r.status_code == 303
    from sqlmodel import select
    rows = (await db.exec(
        select(InboxReply).where(InboxReply.inbox_id == inbox_row.id)
    )).all()
    assert rows == []


async def test_my_inbox_reply_rate_limit_redirects_with_flag(
    client, db, shop, customer, inbox_row, stub_events_publish
):
    """4th customer reply within the window 303s to the detail page with
    ?rate_limited=1 so the template can surface the cooldown notice."""
    shop.allow_customer_messages = True
    db.add(shop)
    await db.commit()
    _set_customer_cookie(client, customer.id)

    for i in range(3):
        ok = await client.post(
            f"/my-inbox/{inbox_row.id}/reply",
            data={"body": f"r{i}"},
            follow_redirects=False,
        )
        assert ok.status_code == 303

    r = await client.post(
        f"/my-inbox/{inbox_row.id}/reply",
        data={"body": "overflow"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "rate_limited=1" in r.headers["location"]


async def test_my_inbox_detail_blocks_other_customer(
    client, db, shop, customer, inbox_row
):
    """A different customer must not see someone else's broadcast."""
    intruder_user = User()
    db.add(intruder_user)
    await db.commit()
    await db.refresh(intruder_user)
    intruder = Customer(user_id=intruder_user.id, is_anonymous=True)
    db.add(intruder)
    await db.commit()
    await db.refresh(intruder)

    _set_customer_cookie(client, intruder.id)
    r = await client.get(f"/my-inbox/{inbox_row.id}")
    assert r.status_code == 404


# ── Shop side ──────────────────────────────────────────────────────────────


async def test_shop_messages_list_shows_inboxes(
    auth_client, db, shop, customer, inbox_row
):
    """Even a broadcast with zero replies appears in /shop/messages —
    the design surface lets the operator verify the send landed."""
    r = await auth_client.get("/shop/messages")
    assert r.status_code == 200
    # Row link routes to the broadcast detail page. The headline path
    # only fires for campaign-linked rows; this inbox has no campaign so
    # it lands in the "ทั่วไป" bucket — the row card still appears.
    assert f'href="/shop/messages/{inbox_row.id}"' in r.text
    assert "ทั่วไป" in r.text


async def test_shop_messages_list_unread_chip_on_customer_reply(
    auth_client, db, shop, customer, inbox_row, stub_events_publish
):
    db.add(InboxReply(inbox_id=inbox_row.id, sender="customer", body="ฮัลโหล"))
    await db.commit()

    body = (await auth_client.get("/shop/messages")).text
    # New chip → unread count of 1 surfaces on the row.
    assert "ctx-unread" in body
    assert "1 ใหม่" in body


async def test_shop_messages_thread_marks_read_and_renders(
    auth_client, db, shop, customer, inbox_row, stub_events_publish
):
    """GET /shop/messages/{id} stamps shop_read_at on every customer
    reply and renders the thread + inline reply textarea."""
    reply = InboxReply(inbox_id=inbox_row.id, sender="customer", body="หวัดดีค่ะ")
    db.add(reply)
    await db.commit()
    await db.refresh(reply)
    assert reply.shop_read_at is None

    r = await auth_client.get(f"/shop/messages/{inbox_row.id}")
    assert r.status_code == 200
    assert "หวัดดีค่ะ" in r.text
    assert 'name="body"' in r.text  # inline reply textarea

    await db.refresh(reply)
    assert reply.shop_read_at is not None


async def test_shop_messages_thread_404_for_other_shop(
    auth_client, db, customer, inbox_row
):
    """Inbox row belonging to another shop must 404 — auth_client is
    bound to `shop` from the fixture, inbox_row belongs to that same
    shop, so we make a parallel inbox under a different shop."""
    from app.models import Inbox, Shop
    other = Shop(name="Other", phone="0899999999", reward_threshold=5)
    db.add(other)
    await db.commit()
    await db.refresh(other)
    other_inbox = Inbox(customer_id=customer.id, shop_id=other.id, body="x")
    db.add(other_inbox)
    await db.commit()
    await db.refresh(other_inbox)

    r = await auth_client.get(f"/shop/messages/{other_inbox.id}")
    assert r.status_code == 404


async def test_shop_messages_reply_persists_and_redirects(
    auth_client, db, shop, customer, inbox_row, stub_events_publish
):
    r = await auth_client.post(
        f"/shop/messages/{inbox_row.id}/reply",
        data={"body": "ขอบคุณที่ติดต่อค่ะ"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/shop/messages/{inbox_row.id}"

    from sqlmodel import select
    rows = (await db.exec(
        select(InboxReply).where(InboxReply.inbox_id == inbox_row.id)
    )).all()
    assert [r.sender for r in rows] == ["shop"]
    assert rows[0].body == "ขอบคุณที่ติดต่อค่ะ"

    # SSE fired toward the customer side so their open page picks it up.
    customer_evts = [t for t in stub_events_publish if t[0] == "customer"]
    assert any(t[2] == "inbox-reply-in" for t in customer_evts)


async def test_shop_messages_reply_empty_body_303s_without_insert(
    auth_client, db, shop, inbox_row
):
    r = await auth_client.post(
        f"/shop/messages/{inbox_row.id}/reply",
        data={"body": "   "},
        follow_redirects=False,
    )
    assert r.status_code == 303

    from sqlmodel import select
    rows = (await db.exec(
        select(InboxReply).where(InboxReply.inbox_id == inbox_row.id)
    )).all()
    assert rows == []


async def test_shop_reply_resets_customer_inbox_read_at(
    auth_client, db, shop, customer, inbox_row, stub_events_publish
):
    """Shop reply on an already-read broadcast pulls it back to unread
    on the customer side."""
    from app.models.util import utcnow
    inbox_row.read_at = utcnow()
    db.add(inbox_row)
    await db.commit()

    r = await auth_client.post(
        f"/shop/messages/{inbox_row.id}/reply",
        data={"body": "ทักกลับค่ะ"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    await db.refresh(inbox_row)
    assert inbox_row.read_at is None
