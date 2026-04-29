"""DeeReach send route — owner can send; staff need can_deereach permission."""

from datetime import timedelta

from sqlmodel import select

from app.core.auth import SESSION_COOKIE_NAME
from app.models import Customer, DeeReachCampaign, Point, StaffMember
from app.models.util import utcnow
from app.services.auth import issue_session_token


async def _seed_unredeemed(db, shop):
    """Point count at goal, last visit ≥7 days ago — qualifies for unredeemed_reward."""
    c = Customer(is_anonymous=False, line_id="U_target")
    db.add(c)
    await db.commit()
    await db.refresh(c)
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


async def test_send_with_customer_subset_records_only_selected(auth_client, db, shop):
    """Owner deselects half the audience — only selected ids end up in the
    campaign + DeeReachMessage rows."""
    from app.models import Customer, DeeReachMessage
    from app.models.util import utcnow
    from datetime import timedelta

    # Two reachable + at-goal customers — both qualify for unredeemed_reward.
    c1 = Customer(is_anonymous=False, line_id="U_a")
    c2 = Customer(is_anonymous=False, line_id="U_b")
    db.add_all([c1, c2])
    await db.commit()
    await db.refresh(c1); await db.refresh(c2)
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


async def test_staff_without_permission_403(client, db, shop):
    """Staff session without can_deereach must be blocked."""
    staff = StaffMember(
        shop_id=shop.id,
        phone="0899999999",
        can_void=True,
        can_deereach=False,  # ← explicit
        accepted_at=utcnow(),
    )
    db.add(staff)
    await db.commit()
    await db.refresh(staff)

    client.cookies.set(
        SESSION_COOKIE_NAME, issue_session_token(shop.id, staff_id=staff.id)
    )
    response = await client.post(
        "/shop/deereach/send", data={"kind": "unredeemed_reward"}, follow_redirects=False
    )
    assert response.status_code == 403
