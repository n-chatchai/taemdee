"""Customer inbox + Web Push subscription routes — smoke + ownership."""

from sqlmodel import select

from app.core.auth import CUSTOMER_COOKIE_NAME
from app.models import Customer, Inbox
from app.services.auth import issue_customer_token


async def _make_customer_with_inbox(db, shop, count: int = 1):
    c = Customer(is_anonymous=True)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    rows = []
    for i in range(count):
        row = Inbox(customer_id=c.id, shop_id=shop.id, body=f"msg {i}")
        db.add(row)
        rows.append(row)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return c, rows


def _set_customer_cookie(client, customer_id):
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(customer_id))


async def test_my_inbox_lists_messages_for_owner(client, db, shop):
    customer, [row] = await _make_customer_with_inbox(db, shop, count=1)
    _set_customer_cookie(client, customer.id)

    r = await client.get("/my-inbox")
    assert r.status_code == 200
    assert "msg 0" in r.text
    assert "กล่องข้อความ" in r.text


async def test_my_inbox_empty_state_renders(client):
    r = await client.get("/my-inbox")
    assert r.status_code == 200
    assert "ยังไม่มีข้อความ" in r.text


async def test_my_inbox_mark_read_flips_read_at(client, db, shop):
    customer, [row] = await _make_customer_with_inbox(db, shop, count=1)
    _set_customer_cookie(client, customer.id)

    r = await client.post(f"/my-inbox/{row.id}/read")
    assert r.status_code == 200

    # Invalidate the test session cache so we see the route's commit.
    await db.refresh(row)
    assert row.read_at is not None


async def test_my_inbox_mark_read_blocks_other_customer(client, db, shop):
    """A different customer must not be able to flip someone else's row."""
    owner, [row] = await _make_customer_with_inbox(db, shop, count=1)
    intruder = Customer(is_anonymous=True)
    db.add(intruder)
    await db.commit()
    await db.refresh(intruder)

    _set_customer_cookie(client, intruder.id)
    r = await client.post(f"/my-inbox/{row.id}/read")
    assert r.status_code == 404


async def test_push_vapid_public_503_when_unconfigured(client):
    r = await client.get("/push/vapid-public")
    assert r.status_code == 503


async def test_push_subscribe_persists_keys_on_customer(client, db):
    r = await client.post(
        "/push/subscribe",
        data={
            "endpoint": "https://fcm.googleapis.com/wp/abc",
            "p256dh": "BPubKeyBase64",
            "auth": "AuthKeyBase64",
        },
    )
    assert r.status_code == 200

    rows = (await db.exec(select(Customer))).all()
    # One anonymous customer was just created via cookie + persisted with keys.
    assert any(
        c.web_push_endpoint == "https://fcm.googleapis.com/wp/abc"
        and c.web_push_p256dh == "BPubKeyBase64"
        and c.web_push_auth == "AuthKeyBase64"
        for c in rows
    )


async def test_push_status_reports_vapid_and_endpoint(client, db, monkeypatch):
    """Diagnostic endpoint: vapid_configured True iff env+DB has the public
    key, has_endpoint reflects the customer's saved subscription state,
    endpoint_prefix is the first 60 chars of whatever's stored."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "web_push_vapid_public_key", "BPubKey")

    # Empty customer first → flags False / empty.
    r0 = await client.get("/push/status")
    assert r0.status_code == 200
    j0 = r0.json()
    assert j0["vapid_configured"] is True
    assert j0["has_endpoint"] is False
    assert j0["endpoint_prefix"] == ""

    # Subscribe with the same client (cookie carried over) → reflected.
    sub = await client.post(
        "/push/subscribe",
        data={
            "endpoint": "https://fcm.googleapis.com/wp/abc123/" + "x" * 80,
            "p256dh": "p", "auth": "a",
        },
    )
    assert sub.status_code == 200

    r1 = await client.get("/push/status")
    j1 = r1.json()
    assert j1["has_endpoint"] is True
    assert j1["endpoint_prefix"].startswith("https://fcm.googleapis.com/wp/abc123/")
    assert len(j1["endpoint_prefix"]) <= 60


async def test_push_unsubscribe_clears_keys(client, db):
    # First subscribe, then unsubscribe — same cookie/customer.
    sub = await client.post(
        "/push/subscribe",
        data={"endpoint": "https://x", "p256dh": "p", "auth": "a"},
    )
    assert sub.status_code == 200

    unsub = await client.post("/push/unsubscribe")
    assert unsub.status_code == 200

    rows = (await db.exec(select(Customer))).all()
    assert all(c.web_push_endpoint is None for c in rows)
