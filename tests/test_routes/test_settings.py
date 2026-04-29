"""Settings sub-pages — S10.identity / S10.location / S10.contact.

The S10 redesign split the legacy single 'ตั้งค่าร้าน' view into 4 sections
with 3 dedicated edit pages. These smoke tests cover the GET render + POST
save path for each so a typo in the route or template name is caught
before deploy.
"""
from sqlmodel import select

from app.models import Shop


async def test_settings_index_renders_4_sections(auth_client):
    response = await auth_client.get("/shop/settings")
    assert response.status_code == 200
    body = response.text
    # Each of the 4 reorg'd sections is present
    for label in ("แต้ม", "ร้าน", "ทีม", "เครดิต"):
        assert f">{label}<" in body, f"Missing section label: {label}"
    # New navigation rows wired in
    assert 'href="/shop/settings/identity"' in body
    assert 'href="/shop/settings/location"' in body
    assert 'href="/shop/settings/contact"' in body


async def test_identity_get_renders_logo_picker(auth_client):
    response = await auth_client.get("/shop/settings/identity")
    assert response.status_code == 200
    body = response.text
    assert "ข้อมูลร้าน" in body
    # Logo gen picker shows ↻ rerolling link
    assert "สร้างใหม่อีก" in body


async def test_identity_post_saves_name_and_logo(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/settings/identity",
        data={"name": "New Cafe", "logo_choice": "lt-2"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/settings"
    await db.refresh(shop)
    assert shop.name == "New Cafe"
    assert shop.logo_url == "text:lt-2"


async def test_location_post_saves_split_address(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/settings/location",
        data={
            "province": "เชียงใหม่",
            "district": "เมืองเชียงใหม่",
            "address_detail": "123 ถ.นิมมานเหมินท์",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    await db.refresh(shop)
    assert shop.location == "เชียงใหม่"
    assert shop.district == "เมืองเชียงใหม่"
    assert shop.address_detail == "123 ถ.นิมมานเหมินท์"


async def test_location_post_blank_clears_fields(auth_client, db, shop):
    """Empty submissions write None — owner can wipe entries via the form."""
    shop.district = "เก่า"
    db.add(shop)
    await db.commit()
    await auth_client.post(
        "/shop/settings/location",
        data={"province": "", "district": "", "address_detail": ""},
        follow_redirects=False,
    )
    await db.refresh(shop)
    assert shop.location is None
    assert shop.district is None
    assert shop.address_detail is None


async def test_contact_post_saves_phone_and_hours(auth_client, db, shop):
    """7-day hours payload roundtrips into the JSON column."""
    data = {"shop_phone": "053-123-4567"}
    for d in ("mon", "tue", "wed", "thu", "fri", "sat"):
        data[f"{d}_open"] = "07:00"
        data[f"{d}_close"] = "18:00"
    data["sun_closed"] = "1"
    data["sun_open"] = "00:00"
    data["sun_close"] = "00:00"

    response = await auth_client.post(
        "/shop/settings/contact",
        data=data,
        follow_redirects=False,
    )
    assert response.status_code == 303
    await db.refresh(shop)
    assert shop.shop_phone == "053-123-4567"
    assert shop.opening_hours["mon"] == {"open": "07:00", "close": "18:00", "closed": False}
    assert shop.opening_hours["sun"]["closed"] is True


async def test_contact_get_renders_default_hours_for_blank_shop(auth_client):
    """Brand-new shops with no opening_hours saved still get a usable form."""
    response = await auth_client.get("/shop/settings/contact")
    assert response.status_code == 200
    body = response.text
    assert "เวลาเปิด-ปิด" in body
    # Default sunday is rendered as closed
    assert "อาทิตย์" in body
