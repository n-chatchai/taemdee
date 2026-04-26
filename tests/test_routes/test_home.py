async def test_home_renders_logged_out(client):
    response = await client.get("/")
    assert response.status_code == 200
    body = response.text
    assert 'href="/shop/login"' in body
    assert 'href="/my-cards"' in body
    assert "/static/css/home.css" in body
    # Logged-out users do NOT see the dashboard link in the nav-cta
    assert 'href="/shop/dashboard"' not in body


async def test_home_swaps_nav_to_dashboard_when_logged_in(auth_client):
    response = await auth_client.get("/")
    assert response.status_code == 200
    body = response.text
    assert 'href="/shop/dashboard"' in body
    assert "แดชบอร์ด" in body
    # The modal trigger link only appears for logged-out visitors
    assert 'href="#login-modal"' not in body


async def test_legacy_register_redirects_to_login(client):
    response = await client.get("/shop/register", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/login"


async def test_legacy_register_preserves_ref_query(client):
    response = await client.get("/shop/register?ref=ABC123", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/login?ref=ABC123"
