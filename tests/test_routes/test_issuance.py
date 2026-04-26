from datetime import timedelta

from sqlmodel import select

from app.models import Customer, Stamp
from app.models.util import utcnow


async def test_phone_entry_creates_customer(auth_client, db):
    response = await auth_client.post(
        "/shop/issue", data={"method": "phone_entry", "phone": "0822222222"}
    )
    assert response.status_code == 200

    result = await db.exec(select(Customer).where(Customer.phone == "0822222222"))
    customer = result.first()
    assert customer is not None
    assert customer.is_anonymous is False


async def test_shop_scan_uses_existing_customer(auth_client, db, shop, customer):
    response = await auth_client.post(
        "/shop/issue", data={"method": "shop_scan", "customer_id": str(customer.id)}
    )
    assert response.status_code == 200

    result = await db.exec(select(Stamp).where(Stamp.customer_id == customer.id))
    stamps = list(result.all())
    assert len(stamps) == 1
    assert stamps[0].issuance_method == "shop_scan"


async def test_manual_issue_creates_anonymous_customer_and_stamp(auth_client, db, shop):
    from uuid import UUID

    response = await auth_client.post("/shop/issue/manual")
    assert response.status_code == 200
    body = response.json()
    assert "stamp_id" in body
    assert "customer_id" in body

    customer = await db.get(Customer, UUID(body["customer_id"]))
    assert customer.is_anonymous is True
    assert customer.phone is None

    stamp = await db.get(Stamp, UUID(body["stamp_id"]))
    assert stamp.shop_id == shop.id
    assert stamp.customer_id == customer.id


async def test_manual_issue_makes_each_call_a_fresh_walk_in(auth_client, db, shop):
    """Each tap of the FAB should produce a distinct anonymous customer so the
    "ลูกค้ากลับมา" headline counts walk-ins as unique visitors."""
    a = (await auth_client.post("/shop/issue/manual")).json()
    b = (await auth_client.post("/shop/issue/manual")).json()
    assert a["customer_id"] != b["customer_id"]

    stamps = list((await db.exec(select(Stamp).where(Stamp.shop_id == shop.id))).all())
    assert len(stamps) == 2


async def test_manual_issue_publishes_toast_event(auth_client, db, shop, monkeypatch):
    from app.routes import issuance as issuance_routes

    received = []

    def fake_publish(shop_id, event_name, html):
        received.append((event_name, html))

    monkeypatch.setattr(issuance_routes, "publish", fake_publish)

    response = await auth_client.post("/shop/issue/manual")
    assert response.status_code == 200

    event_names = [n for n, _ in received]
    assert "feed-row" in event_names
    assert "stamp-toast" in event_names


async def test_invalid_method_400(auth_client):
    response = await auth_client.post("/shop/issue", data={"method": "telepathy"})
    assert response.status_code == 400


async def test_unauthenticated_issue_401(client):
    response = await client.post(
        "/shop/issue", data={"method": "phone_entry", "phone": "0811"}
    )
    assert response.status_code == 401


async def test_void_within_window(auth_client, db, shop, customer):
    issued = await auth_client.post(
        "/shop/issue", data={"method": "shop_scan", "customer_id": str(customer.id)}
    )
    stamp_id = issued.json()["stamp_id"]

    response = await auth_client.post(f"/shop/stamps/{stamp_id}/void")
    assert response.status_code == 200
    assert response.json()["voided"] is True


async def test_void_after_window_400(auth_client, db, shop, customer):
    old = Stamp(
        shop_id=shop.id,
        customer_id=customer.id,
        issuance_method="shop_scan",
        created_at=utcnow() - timedelta(seconds=120),
    )
    db.add(old)
    await db.commit()
    await db.refresh(old)

    response = await auth_client.post(f"/shop/stamps/{old.id}/void")
    assert response.status_code == 400
