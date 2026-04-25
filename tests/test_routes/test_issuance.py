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
