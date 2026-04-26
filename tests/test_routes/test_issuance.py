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


async def test_save_issuance_methods_persists_toggles(auth_client, db, shop):
    """S5 toggle picker — POST /shop/issue/methods saves the 3 booleans."""
    response = await auth_client.post(
        "/shop/issue/methods",
        data={"shop_scan": "1", "phone_entry": "1", "search": "0"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/settings"

    await db.refresh(shop)
    assert shop.issue_method_shop_scan is True
    assert shop.issue_method_phone_entry is True
    assert shop.issue_method_search is False


async def test_save_issuance_methods_clears_when_all_off(auth_client, db, shop):
    shop.issue_method_shop_scan = True
    shop.issue_method_phone_entry = True
    shop.issue_method_search = True
    db.add(shop)
    await db.commit()

    response = await auth_client.post(
        "/shop/issue/methods",
        data={"shop_scan": "0", "phone_entry": "0", "search": "0"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    await db.refresh(shop)
    assert shop.issue_method_shop_scan is False
    assert shop.issue_method_phone_entry is False
    assert shop.issue_method_search is False


async def test_search_customers_returns_match_by_name(auth_client, db, shop):
    from app.models import Customer
    c1 = Customer(is_anonymous=False, display_name="สมศรี", phone="0812345678")
    c2 = Customer(is_anonymous=False, display_name="John", phone="0899999999")
    c3 = Customer(is_anonymous=True, display_name="Anon")
    db.add_all([c1, c2, c3])
    await db.commit()

    r = await auth_client.get("/shop/issue/search/customers?q=สมศรี")
    assert r.status_code == 200
    body = r.json()
    names = [r["display_name"] for r in body["results"]]
    assert "สมศรี" in names
    assert "John" not in names
    # Anonymous customers must NEVER surface in search results
    assert "Anon" not in names


async def test_search_customers_returns_match_by_phone(auth_client, db, shop):
    from app.models import Customer
    db.add(Customer(is_anonymous=False, display_name="X", phone="0812345678"))
    await db.commit()

    r = await auth_client.get("/shop/issue/search/customers?q=0812")
    assert r.status_code == 200
    assert any("0812345678" == r["phone"] for r in r.json()["results"])


async def test_search_customers_empty_q_returns_empty(auth_client):
    r = await auth_client.get("/shop/issue/search/customers?q=")
    assert r.status_code == 200
    assert r.json() == {"results": []}


async def test_search_grant_issues_n_stamps_and_publishes_toast(auth_client, db, shop, monkeypatch):
    from app.models import Customer
    from app.routes import issuance as issuance_routes
    received = []
    monkeypatch.setattr(issuance_routes, "publish", lambda sid, name, html: received.append((name, html)))

    c = Customer(is_anonymous=False, display_name="สมศรี", phone="0812345678")
    db.add(c)
    await db.commit()
    await db.refresh(c)

    response = await auth_client.post(
        "/shop/issue/search/grant",
        data={"customer_id": str(c.id), "points": "3"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["granted"] == 3
    assert len(body["stamp_ids"]) == 3

    stamps = (await db.exec(select(Stamp).where(Stamp.customer_id == c.id))).all()
    assert len(list(stamps)) == 3

    # 3 feed-row events + 1 stamp-toast (toast only fires once after the batch)
    assert sum(1 for n, _ in received if n == "feed-row") == 3
    assert sum(1 for n, _ in received if n == "stamp-toast") == 1


async def test_search_grant_caps_points_at_10(auth_client, db, shop):
    from app.models import Customer
    c = Customer(is_anonymous=False, display_name="X", phone="0812345678")
    db.add(c)
    await db.commit()
    await db.refresh(c)

    response = await auth_client.post(
        "/shop/issue/search/grant",
        data={"customer_id": str(c.id), "points": "999"},
    )
    assert response.status_code == 200
    assert response.json()["granted"] == 10


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
