async def test_privacy_page_renders(client):
    response = await client.get("/privacy")
    assert response.status_code == 200
    body = response.text
    assert "นโยบายความเป็นส่วนตัว" in body
    assert "ข้อมูลที่เราจัดเก็บ" in body
    assert "support@taemdee.com" in body


async def test_privacy_page_has_contact_info(client):
    """Privacy page should have contact email for inquiries."""
    response = await client.get("/privacy")
    assert response.status_code == 200
    body = response.text
    assert "support@taemdee.com" in body


async def test_privacy_page_has_user_rights(client):
    """Privacy page should mention user rights."""
    response = await client.get("/privacy")
    assert response.status_code == 200
    body = response.text
    assert "สิทธิ์" in body
    assert "ลบ" in body or "ลบข้อมูล" in body


async def test_data_deletion_page_renders(client):
    """Data deletion page should render."""
    response = await client.get("/data-deletion")
    assert response.status_code == 200