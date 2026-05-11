"""LINE webhook tests — message-mirror path (replies from LINE OA chat
land as InboxReply rows on the customer's most recent broadcast).

Signature verify is mocked because the conftest strips line creds for
isolation. We bypass the verify gate by stubbing the helper for the
mirror tests; the existing follow/unfollow handling already has
prod-shape coverage elsewhere."""

import base64
import hashlib
import hmac as _hmac
import json
from datetime import timedelta

import pytest

from app.core.config import settings
from app.models import DeeReachEvent, Inbox, InboxReply
from app.models.util import utcnow
from tests._helpers import make_customer


def _line_sign(body: bytes, secret: str) -> str:
    """Mint a valid X-Line-Signature for the given body + channel
    secret — mirrors the verify_signature implementation."""
    digest = _hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


async def _post_event(client, *, event: dict, secret: str = "test-line-secret"):
    body = json.dumps({"destination": "U_oa", "events": [event]}).encode("utf-8")
    sig = _line_sign(body, secret)
    return await client.post(
        "/webhooks/line",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Line-Signature": sig,
        },
    )


@pytest.fixture(autouse=True)
def _enable_line_secret(monkeypatch):
    """Re-enable the LINE channel secret the conftest strips so
    verify_signature actually has a key to check against."""
    monkeypatch.setattr(settings, "line_oa_channel_secret", "test-line-secret")


@pytest.fixture(autouse=True)
def _stub_mark_as_read(monkeypatch):
    """The webhook fires LINE markAsRead after a successful mirror.
    Stub the network call to a capture-list so tests don't try to
    reach api.line.me. Individual tests can read `called_with` to
    assert the API was hit (or wasn't, for drop paths)."""
    called_with: list = []

    async def fake_mark_as_read(line_id):
        called_with.append(line_id)
        return True

    monkeypatch.setattr(
        "app.routes.webhooks.mark_as_read", fake_mark_as_read,
    )
    return called_with


async def test_line_message_mirrors_into_recent_broadcast(
    client, db, shop, stub_events_publish
):
    """A text message from a customer who received a broadcast in the
    last 48h lands as an InboxReply on that broadcast."""
    cust = await make_customer(db, line_id="U_mirror")
    ibx = Inbox(
        customer_id=cust.id, shop_id=shop.id,
        body="คิดถึงพี่นะ",
        created_at=utcnow() - timedelta(hours=2),
    )
    db.add(ibx)
    await db.commit()
    await db.refresh(ibx)

    r = await _post_event(client, event={
        "type": "message",
        "source": {"userId": "U_mirror"},
        "message": {"type": "text", "text": "ขอบคุณครับ"},
    })
    assert r.status_code == 200

    from sqlmodel import select
    replies = (await db.exec(
        select(InboxReply).where(InboxReply.inbox_id == ibx.id)
    )).all()
    assert len(replies) == 1
    assert replies[0].sender == "customer"
    assert replies[0].body == "ขอบคุณครับ"


async def test_line_message_attributes_to_most_recent_broadcast(
    client, db, shop, stub_events_publish
):
    """Customer received broadcasts from two shops. Reply attaches to
    the MOST RECENT one — LINE chat heuristic ("reply is about the
    latest message you got")."""
    from app.models import Shop
    other_shop = Shop(name="OtherShop", phone="0822000000", reward_threshold=10)
    db.add(other_shop)
    await db.commit()
    await db.refresh(other_shop)

    cust = await make_customer(db, line_id="U_two_shops")
    old_ibx = Inbox(
        customer_id=cust.id, shop_id=other_shop.id, body="old shop",
        created_at=utcnow() - timedelta(hours=10),
    )
    new_ibx = Inbox(
        customer_id=cust.id, shop_id=shop.id, body="new shop",
        created_at=utcnow() - timedelta(hours=1),
    )
    db.add_all([old_ibx, new_ibx])
    await db.commit()
    await db.refresh(new_ibx)

    r = await _post_event(client, event={
        "type": "message",
        "source": {"userId": "U_two_shops"},
        "message": {"type": "text", "text": "แวะแน่นอน"},
    })
    assert r.status_code == 200

    from sqlmodel import select
    replies = (await db.exec(select(InboxReply))).all()
    assert len(replies) == 1
    # Reply lands on the newest broadcast (shop, not other_shop).
    assert replies[0].inbox_id == new_ibx.id


async def test_line_message_drops_when_no_recent_broadcast(
    client, db, shop, stub_events_publish
):
    """No broadcast in the 48h window → reply is logged + ignored
    (customer might just be chatting; we don't fabricate attribution)."""
    cust = await make_customer(db, line_id="U_no_recent")
    # Broadcast > 48h old → outside the attribution window.
    stale = Inbox(
        customer_id=cust.id, shop_id=shop.id, body="old",
        created_at=utcnow() - timedelta(days=3),
    )
    db.add(stale)
    await db.commit()

    r = await _post_event(client, event={
        "type": "message",
        "source": {"userId": "U_no_recent"},
        "message": {"type": "text", "text": "hi"},
    })
    assert r.status_code == 200

    from sqlmodel import select
    replies = (await db.exec(select(InboxReply))).all()
    assert replies == []


async def test_line_message_drops_for_unknown_line_id(
    client, db, shop, stub_events_publish
):
    """LINE userId that doesn't match any User row → silent drop,
    200 OK so LINE doesn't retry forever."""
    r = await _post_event(client, event={
        "type": "message",
        "source": {"userId": "U_stranger"},
        "message": {"type": "text", "text": "anyone home?"},
    })
    assert r.status_code == 200

    from sqlmodel import select
    assert (await db.exec(select(InboxReply))).all() == []


async def test_line_message_ignores_stickers_and_images(
    client, db, shop, stub_events_publish
):
    """Only text messages mirror — stickers / images / location have
    no clean InboxReply.body translation so they're dropped."""
    cust = await make_customer(db, line_id="U_sticker")
    db.add(Inbox(
        customer_id=cust.id, shop_id=shop.id, body="x",
        created_at=utcnow() - timedelta(hours=1),
    ))
    await db.commit()

    r = await _post_event(client, event={
        "type": "message",
        "source": {"userId": "U_sticker"},
        "message": {"type": "sticker", "packageId": "1", "stickerId": "1"},
    })
    assert r.status_code == 200

    from sqlmodel import select
    assert (await db.exec(select(InboxReply))).all() == []


async def test_line_message_fires_engagement_event(
    client, db, shop, stub_events_publish
):
    """The mirrored reply flows through send_reply → engagement log
    picks up a 'replied' event same as an in-app reply would."""
    cust = await make_customer(db, line_id="U_engagement")
    ibx = Inbox(
        customer_id=cust.id, shop_id=shop.id, body="x",
        created_at=utcnow() - timedelta(hours=1),
    )
    db.add(ibx)
    await db.commit()
    await db.refresh(ibx)

    await _post_event(client, event={
        "type": "message",
        "source": {"userId": "U_engagement"},
        "message": {"type": "text", "text": "เจอกันพรุ่งนี้"},
    })

    from sqlmodel import select
    events = (await db.exec(
        select(DeeReachEvent).where(
            DeeReachEvent.inbox_id == ibx.id,
            DeeReachEvent.kind == "replied",
        )
    )).all()
    assert len(events) == 1


async def test_line_message_marks_inbox_as_read(
    client, db, shop, stub_events_publish
):
    """LINE reply flips Inbox.read_at — customer obviously read the
    broadcast before replying in the LINE chat. Without this, the
    customer's own /my-inbox dock badge would still show 1 unread
    after they already engaged."""
    cust = await make_customer(db, line_id="U_mark_read")
    ibx = Inbox(
        customer_id=cust.id, shop_id=shop.id, body="cm",
        created_at=utcnow() - timedelta(hours=1),
    )
    db.add(ibx)
    await db.commit()
    await db.refresh(ibx)
    assert ibx.read_at is None

    r = await _post_event(client, event={
        "type": "message",
        "source": {"userId": "U_mark_read"},
        "message": {"type": "text", "text": "อ่านแล้วน้า"},
    })
    assert r.status_code == 200

    await db.refresh(ibx)
    assert ibx.read_at is not None

    # And the customer-side dock badge SSE was fired so any open web
    # tab updates without a refresh.
    names = [t[2] for t in stub_events_publish if t[0] == "customer"]
    assert "inbox-update" in names


async def test_line_message_persists_source_line(
    client, db, shop, stub_events_publish
):
    """The mirrored InboxReply carries source='line' so the shop-side
    thread can render the "ผ่าน LINE" pill — separating it from
    in-app replies (source='app')."""
    cust = await make_customer(db, line_id="U_src")
    db.add(Inbox(
        customer_id=cust.id, shop_id=shop.id, body="x",
        created_at=utcnow() - timedelta(hours=1),
    ))
    await db.commit()

    await _post_event(client, event={
        "type": "message",
        "source": {"userId": "U_src"},
        "message": {"type": "text", "text": "ผ่าน LINE มา"},
    })

    from sqlmodel import select
    reply = (await db.exec(select(InboxReply))).first()
    assert reply is not None
    assert reply.source == "line"


async def test_in_app_reply_persists_source_app(
    client, db, shop, customer, inbox_row, stub_events_publish
):
    """The /my-inbox/<id>/reply POST path doesn't pass a source, so
    send_reply's 'app' default applies — in-app replies stay quiet
    (no pill rendered)."""
    shop.allow_customer_messages = True
    db.add(shop)
    await db.commit()

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(customer.id))
    await client.post(
        f"/my-inbox/{inbox_row.id}/reply",
        data={"body": "ในแอป"},
        follow_redirects=False,
    )

    from sqlmodel import select
    reply = (await db.exec(select(InboxReply))).first()
    assert reply is not None
    assert reply.source == "app"


async def test_line_message_calls_mark_as_read_after_mirror(
    client, db, shop, stub_events_publish, _stub_mark_as_read
):
    """Successful reply mirror also pings LINE markAsRead so the OA
    Manager inbox stops nagging the operator."""
    cust = await make_customer(db, line_id="U_markread")
    db.add(Inbox(
        customer_id=cust.id, shop_id=shop.id, body="x",
        created_at=utcnow() - timedelta(hours=1),
    ))
    await db.commit()

    await _post_event(client, event={
        "type": "message",
        "source": {"userId": "U_markread"},
        "message": {"type": "text", "text": "got it"},
    })

    assert _stub_mark_as_read == ["U_markread"]


async def test_line_message_skips_mark_as_read_on_drop(
    client, db, _stub_mark_as_read
):
    """Drop paths (no user / no broadcast in window) must NOT call
    markAsRead — we only drop the LINE-side badge when our side
    actually captured the message."""
    await _post_event(client, event={
        "type": "message",
        "source": {"userId": "U_nonexistent"},
        "message": {"type": "text", "text": "hi"},
    })

    assert _stub_mark_as_read == []


async def test_line_message_rejects_bad_signature(client, db):
    """A request with a forged X-Line-Signature is rejected before
    any DB lookup — 401, no InboxReply created."""
    body = json.dumps({"events": [{"type": "message"}]}).encode("utf-8")
    r = await client.post(
        "/webhooks/line",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Line-Signature": "garbage",
        },
    )
    assert r.status_code == 401
