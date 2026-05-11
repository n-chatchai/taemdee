"""S3.customers — full customer list at /shop/customers.

Smoke + filter + search coverage so the new tab page doesn't regress as the
backend stats query evolves.
"""
from datetime import timedelta

from app.models import Customer, Point, Redemption
from app.models.util import utcnow
from tests._helpers import make_customer


async def _customer(db, name, *, line_id=None):
    return await make_customer(db, display_name=name, line_id=line_id or f"U_{name}")


async def _stamp(db, shop, customer, *, days_ago=0):
    p = Point(
        shop_id=shop.id,
        customer_id=customer.id,
        issuance_method="customer_scan",
        created_at=utcnow() - timedelta(days=days_ago),
    )
    db.add(p)
    await db.commit()
    return p


async def test_customers_page_renders_empty_state(auth_client):
    response = await auth_client.get("/shop/customers")
    assert response.status_code == 200
    body = response.text
    assert "ลูกค้า" in body
    # Glass nav surfaces the sibling tabs. /shop/issue was retired
    # from the dock — issuance lives behind the action hub now.
    assert 'href="/shop/dashboard"' in body
    assert 'href="/shop/insights"' in body
    assert "0 คน" in body


async def test_customers_page_shows_active_count_per_customer(auth_client, db, shop):
    """Each row carries the customer's active stamp count toward the goal."""
    somsri = await _customer(db, "สมศรี")
    for _ in range(7):
        await _stamp(db, shop, somsri, days_ago=0)

    response = await auth_client.get("/shop/customers")
    body = response.text
    assert "สมศรี" in body
    assert ">7<" in body  # 7 active points rendered


async def test_customers_filter_near_only_returns_near_ready(auth_client, db, shop):
    """`?filter=near` keeps only customers within 2 stamps of the threshold
    (gap_max=2, threshold=10 from fixture → active ≥ 8)."""
    almost = await _customer(db, "ใกล้")
    for _ in range(9):
        await _stamp(db, shop, almost, days_ago=1)
    far = await _customer(db, "ไกล")
    for _ in range(3):
        await _stamp(db, shop, far, days_ago=1)

    response = await auth_client.get("/shop/customers?filter=near")
    body = response.text
    assert "ใกล้" in body
    assert "ไกล" not in body
    assert "1 คน" in body


async def test_customers_filter_lapsed_returns_only_old_visits(auth_client, db, shop):
    """`?filter=lapsed` — last visit older than 14 days."""
    # Use names that won't collide with page chrome (the stats card
    # labels "หายไป"/"ใหม่" surface unconditionally).
    lapsed = await _customer(db, "ลาภินทร์")
    await _stamp(db, shop, lapsed, days_ago=30)
    fresh = await _customer(db, "เฟรชชี่")
    await _stamp(db, shop, fresh, days_ago=1)

    response = await auth_client.get("/shop/customers?filter=lapsed")
    body = response.text
    assert "ลาภินทร์" in body
    assert "เฟรชชี่" not in body


async def test_customers_search_substring_matches_display_name(auth_client, db, shop):
    a = await _customer(db, "สมศรี")
    b = await _customer(db, "สมชาย")
    await _stamp(db, shop, a, days_ago=1)
    await _stamp(db, shop, b, days_ago=1)

    response = await auth_client.get("/shop/customers?q=สมศ")
    body = response.text
    assert "สมศรี" in body
    assert "สมชาย" not in body


async def test_customers_just_claimed_renders_check_badge(auth_client, db, shop):
    """A redemption within 24h flips the row to 'รับแล้ว' badge."""
    c = await _customer(db, "ก้อง")
    await _stamp(db, shop, c, days_ago=1)
    db.add(Redemption(shop_id=shop.id, customer_id=c.id))
    await db.commit()

    response = await auth_client.get("/shop/customers")
    body = response.text
    assert "ก้อง" in body
    assert "รับแล้ว" in body


async def test_customers_engagement_chip_hot_when_replied(auth_client, db, shop):
    """A customer with engagement score ≥ 5 gets the 'ขาประจำส่ง' hot
    chip (mint). One replied = 3 + one voucher_claimed = 5 → 8 → hot."""
    from app.models import DeeReachEvent, Inbox
    c = await _customer(db, "ขยัน")
    await _stamp(db, shop, c, days_ago=1)
    # Need a parent inbox row for the event FK.
    ibx = Inbox(customer_id=c.id, shop_id=shop.id, body="hi")
    db.add(ibx)
    await db.commit()
    await db.refresh(ibx)
    db.add_all([
        DeeReachEvent(inbox_id=ibx.id, customer_id=c.id, shop_id=shop.id, kind="replied"),
        DeeReachEvent(inbox_id=ibx.id, customer_id=c.id, shop_id=shop.id, kind="voucher_claimed"),
    ])
    await db.commit()

    body = (await auth_client.get("/shop/customers")).text
    assert "ขาประจำส่ง" in body
    assert "cust-tag eng hot" in body


async def test_customers_engagement_chip_hidden_when_cold(auth_client, db, shop):
    """Customer with zero engagement events shows no chip — keeps
    the row visually quiet (cold is the majority case)."""
    c = await _customer(db, "เงียบ")
    await _stamp(db, shop, c, days_ago=1)
    await db.commit()

    body = (await auth_client.get("/shop/customers")).text
    assert "เงียบ" in body
    # The "เงียบ" tier label must NOT appear as a chip class on the row.
    assert "cust-tag eng" not in body
