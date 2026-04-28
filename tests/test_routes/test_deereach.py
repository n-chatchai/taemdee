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
