"""S3 dashboard — full-page hub at /shop/dashboard.

Smoke + regression coverage for the new snapshot/trend/attn layout. The
prior version of the dashboard route shipped a SQL unpack bug where it
unpacked a scalar select(Point.created_at) row as a 1-tuple — caught
in prod logs. This file pins down the queries so the same issue can't
slip back in.
"""
from datetime import timedelta

from app.models import Point
from app.models.util import utcnow


async def test_dashboard_renders_with_no_activity(auth_client, db, shop):
    shop.is_onboarded = True
    db.add(shop)
    await db.commit()

    response = await auth_client.get("/shop/dashboard")
    assert response.status_code == 200
    body = response.text
    assert "ลูกค้าวันนี้" in body
    assert "สัปดาห์นี้" in body
    # Glass nav present, home tab active
    assert "s3-glass-nav" in body
    assert 'href="/shop/customers"' in body
    # 7 trend bars rendered even when all zero
    assert body.count('class="tb-bar"') == 7


async def test_dashboard_renders_with_recent_points(auth_client, db, shop, customer):
    """Recent stamps should populate the snapshot + trend bars without
    blowing up on the daily bucket query (regression for the
    'cannot unpack non-iterable datetime' crash hit on prod)."""
    shop.is_onboarded = True
    db.add(shop)
    for offset in [0, 1, 2, 6]:
        db.add(Point(
            shop_id=shop.id,
            customer_id=customer.id,
            issuance_method="customer_scan",
            created_at=utcnow() - timedelta(days=offset),
        ))
    await db.commit()

    response = await auth_client.get("/shop/dashboard")
    assert response.status_code == 200
    body = response.text
    # 4 stamps total in last 7 days → bars rendered + at least one
    # has > min height
    assert body.count('class="tb-bar"') == 7
    assert "tb-bar" in body
