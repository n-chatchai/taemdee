async def test_home_renders_logged_out(client):
    response = await client.get("/")
    assert response.status_code == 200
    body = response.text
    assert 'href="/shop/register"' in body
    assert 'href="/my-cards"' in body
    assert "/static/css/home.css" in body
    assert 'href="/shop/dashboard"' not in body


async def test_home_swaps_nav_to_dashboard_when_logged_in(auth_client):
    response = await auth_client.get("/")
    assert response.status_code == 200
    body = response.text
    assert 'href="/shop/dashboard"' in body
    assert "แดชบอร์ด" in body
    assert '<a href="/shop/register" class="secondary">เข้าสู่ระบบ</a>' not in body
