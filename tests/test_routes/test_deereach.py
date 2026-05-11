"""DeeReach send route — owner can send; staff need can_deereach permission."""

from datetime import timedelta

from sqlmodel import select

from app.core.auth import SESSION_COOKIE_NAME
from app.models import Customer, DeeReachCampaign, Point, StaffMember
from app.models.util import utcnow
from app.services.auth import issue_session_token
from tests._helpers import make_customer


async def _seed_unredeemed(db, shop):
    """Point count at goal, last visit ≥7 days ago — qualifies for unredeemed_reward."""
    c = await make_customer(db, line_id="U_target")
    for _ in range(shop.reward_threshold):
        db.add(Point(
            shop_id=shop.id,
            customer_id=c.id,
            issuance_method="customer_scan",
            created_at=utcnow() - timedelta(days=14),
        ))
    await db.commit()
    return c


async def test_send_redirects_and_records(auth_client, db, shop):
    await _seed_unredeemed(db, shop)
    # Per DeeReach v2: credit_balance is in satang (1 credit = 100 satang).
    # 1 LINE recipient → 100 satang cost; give 1000 satang (= 10 credits).
    shop.credit_balance = 1000
    db.add(shop)
    await db.commit()

    response = await auth_client.post(
        "/shop/deereach/send",
        data={"kind": "unredeemed_reward"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    # Per the S13.sent design, send now lands on the confirmation page with
    # the new campaign id (was /shop/dashboard before the DS3 redesign).
    assert response.headers["location"].startswith("/shop/deereach/sent?campaign_id=")

    rows = (await db.exec(
        select(DeeReachCampaign).where(DeeReachCampaign.shop_id == shop.id)
    )).all()
    assert len(rows) == 1
    assert rows[0].kind == "unredeemed_reward"
    # Redirect target points at the just-created campaign.
    assert str(rows[0].id) in response.headers["location"]


async def test_send_with_custom_message_persists_override(auth_client, db, shop):
    """Owner ships a hand-edited body via the form's `message` field — it
    becomes the campaign's message_text instead of the default template."""
    await _seed_unredeemed(db, shop)
    shop.credit_balance = 1000  # satang
    db.add(shop)
    await db.commit()

    custom = "ขอบคุณที่อุดหนุนนะครับ — กลับมารับ Signature ฟรี วันนี้-อาทิตย์นี้!"
    response = await auth_client.post(
        "/shop/deereach/send",
        data={"kind": "unredeemed_reward", "message": custom},
        follow_redirects=False,
    )
    assert response.status_code == 303

    rows = (await db.exec(
        select(DeeReachCampaign).where(DeeReachCampaign.shop_id == shop.id)
    )).all()
    assert len(rows) == 1
    assert rows[0].message_text == custom


async def test_send_blank_message_400(auth_client, db, shop):
    """Empty / whitespace-only message must reject — server treats it as a
    user error and returns 400 with a Thai detail."""
    await _seed_unredeemed(db, shop)
    shop.credit_balance = 1000
    db.add(shop)
    await db.commit()

    response = await auth_client.post(
        "/shop/deereach/send",
        data={"kind": "unredeemed_reward", "message": "   "},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "ข้อความว่าง" in response.json()["detail"]


async def test_deereach_detail_renders_with_empty_audience(auth_client, db, shop):
    """Auto kind w/ no eligible audience (graduated or rate-limited) used
    to 404 'ไม่มีแคมเปญแนะนำชนิดนี้' — now opens the editor in an empty
    state with 0 / 0 recipients so the owner sees what's happening."""
    r = await auth_client.get("/shop/deereach/unredeemed_reward")
    assert r.status_code == 200
    assert "ไม่มีคนค้างรับรางวัล" in r.text


async def test_deereach_unknown_kind_redirects_to_list(auth_client):
    """Stale bookmark / typo'd kind → bounce to /shop/deereach list,
    not a jarring JSON 404."""
    r = await auth_client.get("/shop/deereach/telepathy", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/shop/deereach"


async def test_deereach_manual_detail_renders(auth_client, db, shop):
    """The 'สร้างแคมเปญเอง' editor opens on /shop/deereach/manual even when
    no auto-suggestion fires; audience = every reachable customer of the
    shop, message defaults to empty so the owner writes their own."""
    from datetime import timedelta

    c = await make_customer(db, line_id="U_picked")
    db.add(Point(
        shop_id=shop.id, customer_id=c.id,
        issuance_method="customer_scan",
        created_at=utcnow() - timedelta(days=2),
    ))
    await db.commit()

    r = await auth_client.get("/shop/deereach/manual")
    assert r.status_code == 200
    body = r.text
    assert "ข้อความของคุณเอง" in body
    # Customer row rendered
    assert str(c.id) in body
    # Engagement segment chips wired (phase 4) — rendered even when
    # empty so the bucket structure stays discoverable.
    assert "ตอบบ่อย" in body
    assert "ขาประจำ" in body


async def test_manual_engagement_chip_buckets_warm_and_hot(
    auth_client, db, shop, stub_events_publish
):
    """Engagement chips bucket customers by their phase-3 score:
       · 'engaged' (warm + hot) — any customer with score ≥ 1
       · 'hot' — only customers with score ≥ 5

    Seed three customers with distinct engagement levels and verify
    the chip counts surface correctly in the editor."""
    from app.models import DeeReachEvent, Inbox

    silent = await make_customer(db, line_id="U_silent")
    warm = await make_customer(db, line_id="U_warm")
    hot = await make_customer(db, line_id="U_hot")
    # Manual audience needs each customer to have at least one stamp
    # (the _audience_for helper filters out drive-bys with no points).
    for c in (silent, warm, hot):
        db.add(Point(shop_id=shop.id, customer_id=c.id, issuance_method="customer_scan"))
    await db.commit()

    # Need a parent Inbox row per (customer) for the events to FK to.
    inboxes = {}
    for c in (warm, hot):
        ibx = Inbox(customer_id=c.id, shop_id=shop.id, body="hi")
        db.add(ibx)
        await db.commit()
        await db.refresh(ibx)
        inboxes[c.id] = ibx

    # warm: 1 opened → score 1 → warm tier
    db.add(DeeReachEvent(
        inbox_id=inboxes[warm.id].id,
        customer_id=warm.id, shop_id=shop.id, kind="opened",
    ))
    # hot: 1 replied + 1 voucher_claimed → score 8 → hot tier
    db.add_all([
        DeeReachEvent(
            inbox_id=inboxes[hot.id].id,
            customer_id=hot.id, shop_id=shop.id, kind="replied",
        ),
        DeeReachEvent(
            inbox_id=inboxes[hot.id].id,
            customer_id=hot.id, shop_id=shop.id, kind="voucher_claimed",
        ),
    ])
    await db.commit()

    body = (await auth_client.get("/shop/deereach/manual")).text
    # The chips render with counts — the engaged bucket contains both
    # warm + hot (2), and the hot bucket only the hot customer (1).
    # Count rendering uses .s13d-seg-count; grep the surrounding chip.
    import re
    chips = re.findall(
        r'@click="selectSegment\((\[[^\]]*\])\)">\s*<span>([^<]+)</span>\s*<span class="s13d-seg-count">(\d+)</span>',
        body,
    )
    by_label = {label.strip(): (ids_json, int(count)) for ids_json, label, count in chips}
    assert by_label["ตอบบ่อย"][1] == 2
    assert by_label["ขาประจำ"][1] == 1
    # The hot customer id appears in both chip lists; warm only in
    # the engaged chip.
    assert str(hot.id) in by_label["ตอบบ่อย"][0]
    assert str(hot.id) in by_label["ขาประจำ"][0]
    assert str(warm.id) in by_label["ตอบบ่อย"][0]
    assert str(warm.id) not in by_label["ขาประจำ"][0]
    assert str(silent.id) not in by_label["ตอบบ่อย"][0]


async def test_send_with_customer_subset_records_only_selected(auth_client, db, shop):
    """Owner deselects half the audience — only selected ids end up in the
    campaign + DeeReachMessage rows."""
    from app.models import DeeReachMessage
    from app.models.util import utcnow
    from datetime import timedelta

    # Two reachable + at-goal customers — both qualify for unredeemed_reward.
    c1 = await make_customer(db, line_id="U_a")
    c2 = await make_customer(db, line_id="U_b")
    for c in (c1, c2):
        for _ in range(shop.reward_threshold):
            db.add(Point(
                shop_id=shop.id, customer_id=c.id,
                issuance_method="customer_scan",
                created_at=utcnow() - timedelta(days=14),
            ))
    shop.credit_balance = 1000  # satang — plenty for 1-2 LINE recipients
    db.add(shop)
    await db.commit()

    # Send to c1 only.
    response = await auth_client.post(
        "/shop/deereach/send",
        data={"kind": "unredeemed_reward", "customer_ids": [str(c1.id)]},
        follow_redirects=False,
    )
    assert response.status_code == 303

    campaigns = (await db.exec(
        select(DeeReachCampaign).where(DeeReachCampaign.shop_id == shop.id)
    )).all()
    assert len(campaigns) == 1
    assert campaigns[0].audience_count == 1

    msgs = (await db.exec(
        select(DeeReachMessage).where(DeeReachMessage.campaign_id == campaigns[0].id)
    )).all()
    assert {m.customer_id for m in msgs} == {c1.id}


async def test_send_with_empty_selection_400(auth_client, db, shop):
    """No customer_ids ticked — service rejects with the dedicated message."""
    await _seed_unredeemed(db, shop)
    shop.credit_balance = 1000
    db.add(shop)
    await db.commit()

    response = await auth_client.post(
        "/shop/deereach/send",
        # customer_ids omitted from the form entirely → the route can't
        # tell apart "select-all" from "no selection". Send a sentinel
        # impossible UUID to signal an empty intersection.
        data={
            "kind": "unredeemed_reward",
            "customer_ids": ["00000000-0000-0000-0000-000000000000"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "ไม่ได้เลือกผู้รับ" in response.json()["detail"]


async def test_send_unsupported_kind_400(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/deereach/send", data={"kind": "telepathy"}, follow_redirects=False
    )
    assert response.status_code == 400


async def test_send_no_audience_400(auth_client, shop, db):
    shop.credit_balance = 1000  # satang
    db.add(shop)
    await db.commit()
    response = await auth_client.post(
        "/shop/deereach/send", data={"kind": "win_back"}, follow_redirects=False
    )
    assert response.status_code == 400


async def test_send_insufficient_credits_400(auth_client, db, shop):
    await _seed_unredeemed(db, shop)
    shop.credit_balance = 0
    db.add(shop)
    await db.commit()
    response = await auth_client.post(
        "/shop/deereach/send", data={"kind": "unredeemed_reward"}, follow_redirects=False
    )
    assert response.status_code == 400


async def test_staff_without_permission_403(app_for_test, db, shop):
    """Staff session without can_deereach must be blocked. Use a shop-host
    client so the subdomain bouncer doesn't 303 us before the perm gate."""
    from app.models import User
    from httpx import ASGITransport, AsyncClient
    staff_user = User(phone="0899999999")
    db.add(staff_user)
    await db.commit()
    await db.refresh(staff_user)
    staff = StaffMember(
        shop_id=shop.id,
        user_id=staff_user.id,
        can_void=True,
        can_deereach=False,  # ← explicit
        accepted_at=utcnow(),
    )
    db.add(staff)
    await db.commit()
    await db.refresh(staff)

    transport = ASGITransport(app=app_for_test)
    async with AsyncClient(transport=transport, base_url="https://shop.test") as c:
        c.cookies.set(
            SESSION_COOKIE_NAME, issue_session_token(shop.id, staff_id=staff.id)
        )
        response = await c.post(
            "/shop/deereach/send", data={"kind": "unredeemed_reward"}, follow_redirects=False
        )
        assert response.status_code == 403
