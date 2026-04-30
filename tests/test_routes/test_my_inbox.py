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


async def test_my_inbox_detail_renders_and_auto_marks_read(client, db, shop):
    """GET /my-inbox/{id} renders the detail page, auto-flips read_at,
    and exposes the mute link for an unmuted shop."""
    customer, [row] = await _make_customer_with_inbox(db, shop, count=1)
    _set_customer_cookie(client, customer.id)
    assert row.read_at is None

    r = await client.get(f"/my-inbox/{row.id}")
    assert r.status_code == 200
    body = r.text
    assert row.body in body
    assert shop.name in body
    # Mute link present, with the shop id as data attribute
    assert f'data-mute-shop="{shop.id}"' in body
    # And the row was flipped to read
    await db.refresh(row)
    assert row.read_at is not None


async def test_my_inbox_detail_blocks_other_customer(client, db, shop):
    """A different customer must not be able to view someone else's row."""
    from app.models import Customer
    owner, [row] = await _make_customer_with_inbox(db, shop, count=1)
    intruder = Customer(is_anonymous=True)
    db.add(intruder)
    await db.commit()
    await db.refresh(intruder)

    _set_customer_cookie(client, intruder.id)
    r = await client.get(f"/my-inbox/{row.id}")
    assert r.status_code == 404


async def test_my_inbox_detail_hides_mute_link_when_already_muted(client, db, shop):
    from app.models import CustomerShopMute
    customer, [row] = await _make_customer_with_inbox(db, shop, count=1)
    db.add(CustomerShopMute(customer_id=customer.id, shop_id=shop.id))
    await db.commit()

    _set_customer_cookie(client, customer.id)
    body = (await client.get(f"/my-inbox/{row.id}")).text
    assert 'data-mute-shop' not in body
    assert "ปิดเสียงร้านนี้แล้ว" in body


async def test_my_inbox_detail_renders_offer_card_when_offer_text_set(client, db, shop):
    """Inbox.detail offer card — render only when row.offer_text is non-NULL.
    Shows the design's 'ของฝากจากร้าน' kicker + offer name + 'ใช้ก่อน
    <date>' condition (or the no-expiry fallback)."""
    from datetime import datetime, timezone, timedelta
    customer, [row] = await _make_customer_with_inbox(db, shop, count=1)
    row.offer_text = "ลด ฿20 เมื่อซื้อกาแฟ"
    row.offer_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=21)
    db.add(row)
    await db.commit()

    _set_customer_cookie(client, customer.id)
    body = (await client.get(f"/my-inbox/{row.id}")).text
    assert "inbox-detail-offer" in body
    assert "ของฝากจากร้าน" in body
    assert "ลด ฿20 เมื่อซื้อกาแฟ" in body
    # bkk_short_date filter formats the expiry — expect "ใช้ก่อน " prefix
    assert "ใช้ก่อน " in body


async def test_my_inbox_detail_no_offer_card_when_offer_text_unset(client, db, shop):
    """Plain DeeReach message without an offer renders body only — the
    .inbox-detail-offer block is omitted entirely (not just empty)."""
    customer, [row] = await _make_customer_with_inbox(db, shop, count=1)
    # offer_text stays NULL by default
    _set_customer_cookie(client, customer.id)
    body = (await client.get(f"/my-inbox/{row.id}")).text
    # The offer card container + kicker are absent. (The push prompt
    # overlay also has "มีของฝากจากร้าน" copy, so check the .ido-label
    # kicker class specifically rather than the substring alone.)
    assert "inbox-detail-offer" not in body
    assert "ido-label" not in body


async def test_my_inbox_detail_offer_no_expiry_uses_fallback_copy(client, db, shop):
    """offer_text set but offer_until NULL → 'โชว์หน้านี้ที่ร้านได้เลยครับ'
    fallback copy instead of the dated ใช้ก่อน line."""
    customer, [row] = await _make_customer_with_inbox(db, shop, count=1)
    row.offer_text = "ครัวซองต์ฟรี 1 ชิ้น"
    db.add(row)
    await db.commit()

    _set_customer_cookie(client, customer.id)
    body = (await client.get(f"/my-inbox/{row.id}")).text
    assert "ครัวซองต์ฟรี 1 ชิ้น" in body
    assert "ใช้ก่อน " not in body
    assert "โชว์หน้านี้ที่ร้านได้เลย" in body


async def test_my_inbox_includes_push_prompt_partial(client):
    """Push.prompt partial is mounted on customer pages that include
    footer_mark — JS gates the show, but the markup must exist server-side
    so it can un-hide on first PWA open."""
    body = (await client.get("/my-inbox")).text
    assert 'id="push-prompt"' in body
    # Must start hidden (server-side default), JS un-hides only when
    # PWA + permission default + cooldown clear.
    assert 'display:none' in body or 'display: none' in body
    # Asset reference for the JS that does the gating.
    assert "/static/js/push-prompt.js" in body


async def test_my_inbox_list_rows_link_to_detail(client, db, shop):
    """Each row in the list is now an <a> linking to /my-inbox/{id}."""
    customer, [row] = await _make_customer_with_inbox(db, shop, count=1)
    _set_customer_cookie(client, customer.id)
    body = (await client.get("/my-inbox")).text
    assert f'href="/my-inbox/{row.id}"' in body


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
    # Cache is a module-level global; another test in the run may have
    # populated it. Clear so the route actually consults the empty DB.
    from app.services.web_push import _cache
    _cache["public"] = None
    _cache["private"] = None

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


async def test_push_status_reports_vapid_and_endpoint(client, db):
    """Diagnostic endpoint: vapid_configured True iff app_secrets has the
    public key, has_endpoint reflects the customer's saved subscription
    state, endpoint_prefix is the first 60 chars of whatever's stored."""
    from app.models import AppSecret
    from app.services.web_push import PUB_KEY_NAME, PRIV_KEY_NAME, _cache

    # Seed the keypair via app_secrets (worker would do this on first
    # boot in production) + clear the process-level cache so load_vapid_keys
    # actually hits the DB.
    db.add(AppSecret(name=PUB_KEY_NAME, value="BPubKey"))
    db.add(AppSecret(name=PRIV_KEY_NAME, value="BPrivKeyPEM"))
    await db.commit()
    _cache["public"] = None
    _cache["private"] = None

    r0 = await client.get("/push/status")
    assert r0.status_code == 200
    j0 = r0.json()
    assert j0["vapid_configured"] is True
    assert j0["has_endpoint"] is False
    assert j0["endpoint_prefix"] == ""

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


async def test_notifications_page_renders_with_muted_shops(client, db, shop):
    """C6.notifications lists every shop the customer has muted; the
    template links each to the unmute POST."""
    from app.models import Customer, CustomerShopMute
    c = Customer(is_anonymous=True)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    db.add(CustomerShopMute(customer_id=c.id, shop_id=shop.id))
    await db.commit()

    _set_customer_cookie(client, c.id)

    r = await client.get("/card/account/notifications")
    assert r.status_code == 200
    body = r.text
    assert "การแจ้งเตือน" in body
    assert shop.name in body
    assert f"/card/account/mute/{shop.id}/unmute" in body


async def test_notifications_unmute_deletes_row(client, db, shop):
    from app.models import Customer, CustomerShopMute
    from sqlmodel import select as _select
    c = Customer(is_anonymous=True)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    db.add(CustomerShopMute(customer_id=c.id, shop_id=shop.id))
    await db.commit()

    _set_customer_cookie(client, c.id)

    r = await client.post(f"/card/account/mute/{shop.id}/unmute", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/card/account/notifications"

    rows = (await db.exec(
        _select(CustomerShopMute).where(
            CustomerShopMute.customer_id == c.id, CustomerShopMute.shop_id == shop.id
        )
    )).all()
    assert rows == []


async def test_notifications_channel_save_clears_for_auto(client, db):
    from app.models import Customer

    _set_customer_cookie(client, "fake")  # no-op; route ignores invalid token
    # Subscribe creates anonymous customer + cookie. Use that path so the
    # customer exists for the channel POST.
    sub = await client.post(
        "/push/subscribe",
        data={"endpoint": "https://x", "p256dh": "p", "auth": "a"},
    )
    assert sub.status_code == 200

    r1 = await client.post("/card/account/notifications", data={"channel": "inbox"}, follow_redirects=False)
    assert r1.status_code == 303
    db.expire_all()
    rows = (await db.exec(select(Customer))).all()
    assert any(c.preferred_channel == "inbox" for c in rows)

    r2 = await client.post("/card/account/notifications", data={"channel": "auto"}, follow_redirects=False)
    assert r2.status_code == 303
    db.expire_all()
    rows = (await db.exec(select(Customer))).all()
    assert all(c.preferred_channel is None for c in rows)


async def test_my_inbox_renders_design_aligned_card_markup(client, db, shop):
    """Inbox list now uses the .inbox-card / .ic-* structure from design,
    not the old .inbox-row / .ib-* markup. Mute action lives on the
    detail page only — no row-level mute link any more."""
    customer, [_row] = await _make_customer_with_inbox(db, shop, count=1)
    _set_customer_cookie(client, customer.id)
    body = (await client.get("/my-inbox")).text

    # Design-aligned classes
    assert "inbox-card" in body
    assert "ic-logo" in body
    assert "ic-preview" in body
    assert "page-head" in body
    assert "inbox-filters" in body
    # Mute link is detail-only now
    assert "data-mute-shop" not in body
    # Old markup retired
    assert "ib-mute" not in body


async def test_mute_endpoint_creates_row_and_is_idempotent(client, db, shop):
    from app.models import Customer, CustomerShopMute
    customer = Customer(is_anonymous=True)
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    _set_customer_cookie(client, customer.id)

    r = await client.post(f"/card/account/mute/{shop.id}/mute")
    assert r.status_code == 204
    rows = (await db.exec(
        select(CustomerShopMute).where(
            CustomerShopMute.customer_id == customer.id,
            CustomerShopMute.shop_id == shop.id,
        )
    )).all()
    assert len(rows) == 1

    # Re-mute → still 204, row count unchanged.
    r2 = await client.post(f"/card/account/mute/{shop.id}/mute")
    assert r2.status_code == 204
    rows = (await db.exec(
        select(CustomerShopMute).where(
            CustomerShopMute.customer_id == customer.id,
            CustomerShopMute.shop_id == shop.id,
        )
    )).all()
    assert len(rows) == 1


def test_sse_me_route_is_registered():
    """Verify the customer SSE route is registered. We don't actually open
    a request — httpx + ASGITransport can't cleanly cancel a long-lived
    streaming generator, the test would hang. The publish/subscribe
    roundtrip tests below cover the dispatcher behaviour."""
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/sse/me" in paths


async def test_publish_customer_dispatches_to_local_subscriber(db):
    """events.publish_customer + subscribe_customer roundtrip in-process
    (sqlite test mode — no Postgres NOTIFY, so this exercises the
    fallback dispatcher that the real worker also hits when NOTIFY isn't
    initialised)."""
    import asyncio
    from uuid import uuid4
    from app.services import events

    cid = uuid4()
    q = events.subscribe_customer(cid)
    try:
        events.publish_customer(cid, "inbox-update", "3")
        # Local dispatch is sync; should be on the queue already.
        name, payload = await asyncio.wait_for(q.get(), timeout=1.0)
        assert name == "inbox-update"
        assert payload == "3"
    finally:
        events.unsubscribe_customer(cid, q)


async def test_publish_customer_async_also_dispatches_locally(db):
    """The awaitable variant used from the RQ worker context follows the
    same local fallback when no publisher pool is configured."""
    import asyncio
    from uuid import uuid4
    from app.services import events

    cid = uuid4()
    q = events.subscribe_customer(cid)
    try:
        await events.publish_customer_async(cid, "inbox-update", "5")
        name, payload = await asyncio.wait_for(q.get(), timeout=1.0)
        assert name == "inbox-update"
        assert payload == "5"
    finally:
        events.unsubscribe_customer(cid, q)


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
