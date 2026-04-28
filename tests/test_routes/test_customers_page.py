"""S3.customers — full customer list at /shop/customers.

Smoke + filter + search coverage so the new tab page doesn't regress as the
backend stats query evolves.
"""
from datetime import timedelta

from app.models import Customer, Point, Redemption
from app.models.util import utcnow


async def _customer(db, name, *, line_id=None):
    c = Customer(
        is_anonymous=False,
        display_name=name,
        line_id=line_id or f"U_{name}",
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


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
    # Glass nav links to all 4 sibling tabs
    assert 'href="/shop/dashboard"' in body
    assert 'href="/shop/issue"' in body
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
    lapsed = await _customer(db, "หาย")
    await _stamp(db, shop, lapsed, days_ago=30)
    fresh = await _customer(db, "ใหม่")
    await _stamp(db, shop, fresh, days_ago=1)

    response = await auth_client.get("/shop/customers?filter=lapsed")
    body = response.text
    assert "หาย" in body
    assert "ใหม่" not in body


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
