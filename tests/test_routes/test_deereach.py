"""DeeReach send route — owner can send; staff need can_deereach permission."""

from datetime import timedelta

from sqlmodel import select

from app.core.auth import SESSION_COOKIE_NAME
from app.models import Customer, DeeReachCampaign, Stamp, StaffMember
from app.models.util import utcnow
from app.services.auth import issue_session_token


async def _seed_unredeemed(db, shop):
    """Stamp count at goal, last visit ≥7 days ago — qualifies for unredeemed_reward."""
    c = Customer(is_anonymous=False, line_id="U_target")
    db.add(c)
    await db.commit()
    await db.refresh(c)
    for _ in range(shop.reward_threshold):
        db.add(Stamp(
            shop_id=shop.id,
            customer_id=c.id,
            issuance_method="customer_scan",
            created_at=utcnow() - timedelta(days=14),
        ))
    await db.commit()
    return c


async def test_send_redirects_and_records(auth_client, db, shop):
    await _seed_unredeemed(db, shop)
    shop.credit_balance = 10
    db.add(shop)
    await db.commit()

    response = await auth_client.post(
        "/shop/deereach/send",
        data={"kind": "unredeemed_reward"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/dashboard"

    rows = (await db.exec(
        select(DeeReachCampaign).where(DeeReachCampaign.shop_id == shop.id)
    )).all()
    assert len(rows) == 1
    assert rows[0].kind == "unredeemed_reward"


async def test_send_unsupported_kind_400(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/deereach/send", data={"kind": "telepathy"}, follow_redirects=False
    )
    assert response.status_code == 400


async def test_send_no_audience_400(auth_client, shop, db):
    shop.credit_balance = 10
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
