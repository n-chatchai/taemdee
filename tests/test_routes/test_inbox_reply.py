"""HTTP integration tests for the broadcast-scoped reply flow.

Covers both sides:
  · customer  → /my-inbox/{id} GET + reply POST
  · shop      → /shop/messages, /shop/messages/{id}, reply POST

Uses the conftest `client`, `shop`, `customer`, `inbox_row`, `auth_client`
fixtures + the stub_events_publish autouse fixture so the SSE publish
calls don't try to reach a real Postgres."""

from app.core.auth import CUSTOMER_COOKIE_NAME
from app.models import Customer, Inbox, InboxReply, User
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
    # Reply row routes to the (broadcast, customer) thread; the
    # inbox row has no campaign so it lands under the "ทั่วไป" bucket
    # but the row link itself uses the inbox id.
    assert f'href="/shop/messages/{inbox_row.id}"' in r.text
    assert "ทั่วไป" in r.text
    # Design-aligned class names for the new grouped layout.
    assert "ib-broadcast" in r.text
    assert "ib-reply" in r.text


async def test_shop_messages_list_unread_chip_on_customer_reply(
    auth_client, db, shop, customer, inbox_row, stub_events_publish
):
    db.add(InboxReply(inbox_id=inbox_row.id, sender="customer", body="ฮัลโหล"))
    await db.commit()

    body = (await auth_client.get("/shop/messages")).text
    # Per design's inbox.list refactor: unread customers wear the
    # .ib-reply.unread modifier (pulsing dot via ::before, no count
    # chip) instead of a per-row "N ใหม่" chip. The aggregate unread
    # count surfaces on the page-head sub line.
    assert "ib-reply unread" in body
    assert "1 ยังไม่ได้ตอบ" in body


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

    # Seed a customer reply so the shop is allowed to respond (the
    # can_reply gate requires the last message to be from the
    # customer; sending the route still works either way, but render
    # the form precondition here for realism).
    db.add(InboxReply(inbox_id=inbox_row.id, sender="customer", body="hi"))
    await db.commit()

    r = await auth_client.post(
        f"/shop/messages/{inbox_row.id}/reply",
        data={"body": "ทักกลับค่ะ"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    await db.refresh(inbox_row)
    assert inbox_row.read_at is None


# ── "Ball in whose court" gate ─────────────────────────────────────────────


async def test_customer_can_reply_to_fresh_broadcast(
    client, db, shop, customer, inbox_row
):
    """Zero replies after a broadcast = shop spoke last (the broadcast
    itself). Customer should see the inline reply form."""
    shop.allow_customer_messages = True
    db.add(shop)
    await db.commit()

    _set_customer_cookie(client, customer.id)
    body = (await client.get(f"/my-inbox/{inbox_row.id}")).text
    assert 'id="ix-compose"' in body
    # The waiting placeholder div must not be present when the form is
    # shown. (The string "รอร้านตอบกลับ" also appears in the optimistic
    # JS handler, so check the wrapper div instead.)
    assert 'class="ix-compose-waiting"' not in body


async def test_customer_form_hides_after_their_own_reply(
    client, db, shop, customer, inbox_row, stub_events_publish
):
    """After the customer replies, the form swaps for the waiting
    placeholder until the shop responds — stops the customer from
    chaining messages."""
    shop.allow_customer_messages = True
    db.add(shop)
    db.add(InboxReply(inbox_id=inbox_row.id, sender="customer", body="แวะแน่"))
    await db.commit()

    _set_customer_cookie(client, customer.id)
    body = (await client.get(f"/my-inbox/{inbox_row.id}")).text
    assert 'id="ix-compose"' not in body
    assert 'class="ix-compose-waiting"' in body


async def test_customer_form_returns_after_shop_replies(
    client, db, shop, customer, inbox_row, stub_events_publish
):
    """Once the shop responds, the ball is back in the customer's
    court and the reply form re-appears."""
    shop.allow_customer_messages = True
    db.add(shop)
    db.add_all([
        InboxReply(inbox_id=inbox_row.id, sender="customer", body="แวะแน่"),
        InboxReply(inbox_id=inbox_row.id, sender="shop", body="ขอบคุณค่ะ"),
    ])
    await db.commit()

    _set_customer_cookie(client, customer.id)
    body = (await client.get(f"/my-inbox/{inbox_row.id}")).text
    assert 'id="ix-compose"' in body


async def test_shop_form_hidden_when_no_replies_yet(
    auth_client, db, shop, customer, inbox_row
):
    """Right after the shop sent a broadcast (zero replies), the shop
    must NOT see a reply form — they spoke last, ball is with the
    customer."""
    body = (await auth_client.get(f"/shop/messages/{inbox_row.id}")).text
    assert 'id="ix-compose"' not in body
    # The waiting div is what gates the form; the "รอลูกค้าตอบกลับ"
    # copy also appears in the optimistic JS handler so check the
    # wrapper class.
    assert 'class="ix-compose-waiting"' in body


async def test_shop_form_shows_after_customer_reply(
    auth_client, db, shop, customer, inbox_row, stub_events_publish
):
    """Customer reply puts the ball in the shop's court — form appears."""
    db.add(InboxReply(inbox_id=inbox_row.id, sender="customer", body="ขอบคุณค่ะ"))
    await db.commit()

    body = (await auth_client.get(f"/shop/messages/{inbox_row.id}")).text
    assert 'id="ix-compose"' in body


async def test_shop_thread_renders_line_pill_for_line_sourced_reply(
    auth_client, db, shop, customer, inbox_row, stub_events_publish
):
    """Replies with source='line' render the "ผ่านไลน์" pill on the
    shop's thread page — operator can spot which channel the customer
    used at a glance. In-app replies (source='app') stay pill-less."""
    db.add_all([
        InboxReply(
            inbox_id=inbox_row.id, sender="customer",
            body="from LINE", source="line",
        ),
        InboxReply(
            inbox_id=inbox_row.id, sender="customer",
            body="from app", source="app",
        ),
    ])
    await db.commit()

    body = (await auth_client.get(f"/shop/messages/{inbox_row.id}")).text
    # Pill renders for the LINE reply only.
    assert body.count("ผ่านไลน์") == 1
    assert "ixc-via line" in body


async def test_shop_form_hides_after_their_own_reply(
    auth_client, db, shop, customer, inbox_row, stub_events_publish
):
    """Once the shop responds, the form hides again until the customer
    replies back."""
    db.add_all([
        InboxReply(inbox_id=inbox_row.id, sender="customer", body="ขอบคุณค่ะ"),
        InboxReply(inbox_id=inbox_row.id, sender="shop", body="ยินดีค่ะ"),
    ])
    await db.commit()

    body = (await auth_client.get(f"/shop/messages/{inbox_row.id}")).text
    assert 'id="ix-compose"' not in body


# ── Engagement event log integration ───────────────────────────────────────


async def test_my_inbox_detail_logs_opened_event(
    client, db, shop, customer, inbox_row
):
    """First GET on /my-inbox/<id> flips read_at + logs an 'opened'
    DeeReachEvent. Subsequent opens dedupe — exactly one row stays."""
    from sqlmodel import select
    from app.models import DeeReachEvent
    from app.services.deereach_events import KIND_OPENED

    _set_customer_cookie(client, customer.id)
    await client.get(f"/my-inbox/{inbox_row.id}")
    await client.get(f"/my-inbox/{inbox_row.id}")  # second open — no-op log

    rows = (await db.exec(
        select(DeeReachEvent).where(
            DeeReachEvent.inbox_id == inbox_row.id,
            DeeReachEvent.kind == KIND_OPENED,
        )
    )).all()
    assert len(rows) == 1
    assert rows[0].customer_id == customer.id
    assert rows[0].shop_id == shop.id


async def test_my_inbox_mark_read_logs_opened_event(
    client, db, shop, customer, inbox_row
):
    """The list-level POST /my-inbox/<id>/read endpoint also logs
    'opened' — the customer dismissing from the list still counts as
    engagement."""
    from sqlmodel import select
    from app.models import DeeReachEvent
    from app.services.deereach_events import KIND_OPENED

    _set_customer_cookie(client, customer.id)
    r = await client.post(f"/my-inbox/{inbox_row.id}/read")
    assert r.status_code == 200

    rows = (await db.exec(
        select(DeeReachEvent).where(
            DeeReachEvent.inbox_id == inbox_row.id,
            DeeReachEvent.kind == KIND_OPENED,
        )
    )).all()
    assert len(rows) == 1


async def test_my_inbox_detail_logs_voucher_claimed_when_offer_set(
    client, db, shop, customer, inbox_row
):
    """A broadcast with offer_text → opening it logs BOTH 'opened' and
    'voucher_claimed' (auto-receive model: viewing the offer = claiming
    it; no explicit claim button)."""
    from sqlmodel import select
    from app.models import DeeReachEvent

    inbox_row.offer_text = "ลด ฿20"
    db.add(inbox_row)
    await db.commit()

    _set_customer_cookie(client, customer.id)
    await client.get(f"/my-inbox/{inbox_row.id}")

    kinds = (await db.exec(
        select(DeeReachEvent.kind).where(DeeReachEvent.inbox_id == inbox_row.id)
    )).all()
    assert set(kinds) == {"opened", "voucher_claimed"}


async def test_my_inbox_detail_no_voucher_event_without_offer(
    client, db, shop, customer, inbox_row
):
    """Broadcast with no offer_text → only 'opened' logs; no voucher
    event is fabricated."""
    from sqlmodel import select
    from app.models import DeeReachEvent

    _set_customer_cookie(client, customer.id)
    await client.get(f"/my-inbox/{inbox_row.id}")

    kinds = (await db.exec(
        select(DeeReachEvent.kind).where(DeeReachEvent.inbox_id == inbox_row.id)
    )).all()
    assert set(kinds) == {"opened"}


async def test_my_inbox_mark_read_logs_voucher_claimed_when_offer_set(
    client, db, shop, customer, inbox_row
):
    """List-level mark-read also fires voucher_claimed when the
    broadcast carries an offer — symmetric with the detail GET path."""
    from sqlmodel import select
    from app.models import DeeReachEvent

    inbox_row.offer_text = "ครัวซองต์ฟรี"
    db.add(inbox_row)
    await db.commit()

    _set_customer_cookie(client, customer.id)
    await client.post(f"/my-inbox/{inbox_row.id}/read")

    kinds = (await db.exec(
        select(DeeReachEvent.kind).where(DeeReachEvent.inbox_id == inbox_row.id)
    )).all()
    assert set(kinds) == {"opened", "voucher_claimed"}


async def test_customer_reply_logs_replied_event(
    client, db, shop, customer, inbox_row, stub_events_publish
):
    """A successful customer reply leaves an audit trail in the
    engagement log — kind='replied' with the reply id in payload."""
    from sqlmodel import select
    from app.models import DeeReachEvent, InboxReply
    from app.services.deereach_events import KIND_REPLIED

    shop.allow_customer_messages = True
    db.add(shop)
    await db.commit()

    _set_customer_cookie(client, customer.id)
    r = await client.post(
        f"/my-inbox/{inbox_row.id}/reply",
        data={"body": "ไปแน่ครับ"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    events = (await db.exec(
        select(DeeReachEvent).where(
            DeeReachEvent.inbox_id == inbox_row.id,
            DeeReachEvent.kind == KIND_REPLIED,
        )
    )).all()
    assert len(events) == 1
    # Payload carries the reply id for timeline drill-down.
    reply = (await db.exec(
        select(InboxReply).where(InboxReply.inbox_id == inbox_row.id)
    )).first()
    assert events[0].payload is not None
    assert str(reply.id) in events[0].payload


async def test_shop_reply_does_not_log_replied_event(
    auth_client, db, shop, customer, inbox_row, stub_events_publish
):
    """Shop-sender replies are operator workflow, not audience
    engagement — they must NOT show up in the engagement log."""
    from sqlmodel import select
    from app.models import DeeReachEvent

    # Seed a customer reply so the shop is allowed to respond.
    db.add(InboxReply(inbox_id=inbox_row.id, sender="customer", body="hi"))
    await db.commit()

    await auth_client.post(
        f"/shop/messages/{inbox_row.id}/reply",
        data={"body": "ขอบคุณค่ะ"},
        follow_redirects=False,
    )

    events = (await db.exec(
        select(DeeReachEvent).where(DeeReachEvent.kind == "replied")
    )).all()
    # The customer's seeded reply went straight to db.add (no service
    # call), so no engagement event was logged for it either. Net
    # count after the shop's reply path must remain zero.
    assert len(events) == 0


# ── Broadcast stats page ───────────────────────────────────────────────────


async def _seed_campaign_with_inboxes(db, shop, customer_count: int = 2):
    """Build a campaign + N inbox rows tied to it. Each inbox gets
    its own User+Customer (audience needs distinct customer_ids for
    the dedup-by-customer aggregate to mean anything)."""
    from app.models import DeeReachCampaign
    from app.models.util import utcnow
    from tests._helpers import make_customer

    campaign = DeeReachCampaign(
        shop_id=shop.id,
        kind="win_back",
        audience_count=customer_count,
        message_text="คิดถึงพี่ครับ\nแวะมานะ",
        offer_label="ลด ฿20",
        final_credits_satang=50,
        sent_at=utcnow(),
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)

    inboxes = []
    for i in range(customer_count):
        c = await make_customer(db, line_id=f"U_aud_{i}", display_name=f"พี่ {i}")
        ibx = Inbox(
            customer_id=c.id,
            shop_id=shop.id,
            campaign_id=campaign.id,
            body=f"คิดถึงพี่ {i}\nแวะมานะ",
        )
        db.add(ibx)
        await db.commit()
        await db.refresh(ibx)
        inboxes.append((c, ibx))
    return campaign, inboxes


async def test_broadcast_stats_empty_engagement(
    auth_client, db, shop
):
    """Right after send (no opens, no replies) — all engagement
    counters render zero against the seeded audience."""
    campaign, inboxes = await _seed_campaign_with_inboxes(db, shop, customer_count=3)

    r = await auth_client.get(f"/shop/messages/broadcast/{campaign.id}")
    assert r.status_code == 200
    body = r.text
    # Audience = 3, opens / replies = 0
    assert ">3<" in body
    assert "เปิดอ่าน" in body
    assert "ตอบกลับ" in body
    # Each customer row renders with the "silent" mark.
    assert body.count("ยังไม่เปิด") == 3


async def test_broadcast_stats_counts_dedup_per_customer(
    auth_client, db, shop, stub_events_publish
):
    """Multiple opens by the same customer count once — engagement
    is per-customer for the headline numbers."""
    from app.models import DeeReachEvent
    from app.services.deereach_events import KIND_OPENED, log_event

    campaign, inboxes = await _seed_campaign_with_inboxes(db, shop, customer_count=2)
    (c1, ibx1), (c2, ibx2) = inboxes

    # c1 opens twice (dedup short-circuits the second log), c2 once.
    await log_event(db, inbox=ibx1, kind=KIND_OPENED)
    await log_event(db, inbox=ibx1, kind=KIND_OPENED)
    await log_event(db, inbox=ibx2, kind=KIND_OPENED)

    r = await auth_client.get(f"/shop/messages/broadcast/{campaign.id}")
    body = r.text
    # Both customers opened — count is 2 even with 3 attempted log calls.
    from sqlmodel import select
    events = (await db.exec(
        select(DeeReachEvent).where(DeeReachEvent.campaign_id == campaign.id)
    )).all()
    assert len(events) == 2  # dedup kicked in
    assert "เปิดอ่านแล้ว" in body
    # 2 of 2 customers show the opened mark
    assert body.count("เปิดอ่านแล้ว") == 2


async def test_broadcast_stats_replied_outranks_opened(
    auth_client, db, shop, stub_events_publish
):
    """Customer who replied surfaces above customers who only opened
    — operators scan the wins first."""
    from app.services.deereach_events import KIND_OPENED, KIND_REPLIED, log_event

    campaign, inboxes = await _seed_campaign_with_inboxes(db, shop, customer_count=3)
    (c1, ibx1), (c2, ibx2), (c3, ibx3) = inboxes

    # c1 stays silent, c2 opens only, c3 replies (which implies opened).
    await log_event(db, inbox=ibx2, kind=KIND_OPENED)
    await log_event(db, inbox=ibx3, kind=KIND_OPENED)
    await log_event(db, inbox=ibx3, kind=KIND_REPLIED, payload={"reply_id": "x"})

    r = await auth_client.get(f"/shop/messages/broadcast/{campaign.id}")
    body = r.text
    # Order in HTML: replied first, opened next, silent last.
    pos_replied = body.index(c3.display_name)
    pos_opened = body.index(c2.display_name)
    pos_silent = body.index(c1.display_name)
    assert pos_replied < pos_opened < pos_silent


async def test_broadcast_stats_404_for_other_shop(auth_client, db):
    """Campaign that belongs to a different shop must 404."""
    from app.models import DeeReachCampaign, Shop
    from app.models.util import utcnow
    other = Shop(name="Other", phone="0899999999", reward_threshold=5)
    db.add(other)
    await db.commit()
    await db.refresh(other)

    cp = DeeReachCampaign(
        shop_id=other.id, kind="manual", audience_count=0,
        sent_at=utcnow(),
    )
    db.add(cp)
    await db.commit()
    await db.refresh(cp)

    r = await auth_client.get(f"/shop/messages/broadcast/{cp.id}")
    assert r.status_code == 404


async def test_broadcast_stats_404_for_missing_campaign(auth_client):
    from uuid import uuid4
    r = await auth_client.get(f"/shop/messages/broadcast/{uuid4()}")
    assert r.status_code == 404


async def test_messages_list_links_to_broadcast_stats(
    auth_client, db, shop
):
    """The ibh-link "ดู →" on /shop/messages now deep-links to the
    broadcast stats page (used to point at /shop/deereach/sent)."""
    campaign, _ = await _seed_campaign_with_inboxes(db, shop, customer_count=1)
    body = (await auth_client.get("/shop/messages")).text
    assert f"/shop/messages/broadcast/{campaign.id}" in body
    assert f"/shop/deereach/sent?campaign_id={campaign.id}" not in body
