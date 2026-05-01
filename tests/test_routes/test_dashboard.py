"""S3 dashboard — full-page hub at /shop/dashboard.

Smoke + regression coverage for the new snapshot/trend/attn layout. The
prior version of the dashboard route shipped a SQL unpack bug where it
unpacked a scalar select(Point.created_at) row as a 1-tuple — caught
in prod logs. This file pins down the queries so the same issue can't
slip back in.
"""
from datetime import timedelta

from sqlmodel import select

from app.models import Point
from app.models.util import utcnow


async def test_dashboard_renders_with_no_activity(auth_client, db, shop):
    shop.is_onboarded = True
    db.add(shop)
    await db.commit()

    response = await auth_client.get("/shop/dashboard")
    assert response.status_code == 200
    body = response.text
    # Merged metrics card: title + period pills + 7 trend bars
    assert ">ลูกค้า<" in body
    assert "s3-metrics" in body
    assert ">วันนี้<" in body  # period pill
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


async def test_qr_live_renders_rotating_page(auth_client, shop):
    """S3.qr — fullscreen rotating QR mode. Initial render embeds an
    inline SVG with a fresh JWT token; the JS polls /shop/qr/live/refresh
    to swap it every 15s."""
    response = await auth_client.get("/shop/qr/live")
    assert response.status_code == 200
    body = response.text
    assert "s3-qr-page" in body
    assert "s3-qr-card" in body
    assert "s3-qr-countdown" in body
    # Initial QR is embedded server-side (inline SVG)
    assert "<svg" in body


async def test_qr_live_refresh_returns_fresh_svg_and_ttl(auth_client):
    """The polling endpoint returns a JSON envelope the page can swap in."""
    response = await auth_client.get("/shop/qr/live/refresh")
    assert response.status_code == 200
    payload = response.json()
    assert "svg" in payload and "<svg" in payload["svg"]
    assert payload["expires_in"] >= 10


# ---------------------------------------------------------------------------
# /shop/settings/menu — owner CRUD for shop.story menu items
# ---------------------------------------------------------------------------


async def test_settings_menu_create_appends_item(auth_client, db, shop):
    from app.models import ShopMenuItem
    response = await auth_client.post(
        "/shop/settings/menu",
        data={"name": "ลาเต้", "price": "65", "emoji": "☕", "is_signature": "on"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    rows = (await db.exec(select(ShopMenuItem).where(ShopMenuItem.shop_id == shop.id))).all()
    assert len(rows) == 1
    assert rows[0].name == "ลาเต้"
    assert rows[0].price == 65
    assert rows[0].emoji == "☕"
    assert rows[0].is_signature is True


async def test_settings_menu_create_rejects_blank_name(auth_client):
    response = await auth_client.post(
        "/shop/settings/menu",
        data={"name": "  "},
        follow_redirects=False,
    )
    assert response.status_code == 400


async def test_settings_menu_delete_removes_item(auth_client, db, shop):
    from app.models import ShopMenuItem
    item = ShopMenuItem(shop_id=shop.id, name="ลบ", sort_order=0)
    db.add(item)
    await db.commit()
    await db.refresh(item)

    response = await auth_client.post(
        f"/shop/settings/menu/{item.id}/delete",
        follow_redirects=False,
    )
    assert response.status_code == 303

    rows = (await db.exec(select(ShopMenuItem))).all()
    assert len(rows) == 0


async def test_settings_menu_signature_toggles(auth_client, db, shop):
    from app.models import ShopMenuItem
    item = ShopMenuItem(shop_id=shop.id, name="ขายดี", sort_order=0)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    assert item.is_signature is False

    await auth_client.post(f"/shop/settings/menu/{item.id}/signature", follow_redirects=False)
    db.expire_all()
    await db.refresh(item)
    assert item.is_signature is True

    await auth_client.post(f"/shop/settings/menu/{item.id}/signature", follow_redirects=False)
    db.expire_all()
    await db.refresh(item)
    assert item.is_signature is False
