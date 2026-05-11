async def test_home_renders_logged_out(client):
    response = await client.get("/")
    assert response.status_code == 200
    body = response.text
    assert 'href="/shop/login"' in body
    assert 'href="/my-cards"' in body
    assert "/static/css/home.css" in body
    # Logged-out users do NOT see the dashboard link in the nav-cta
    assert 'href="/shop/dashboard"' not in body


# Cookie-aware home redirects + /switch role picker were retired —
# `/` always renders the marketing page now and PWA manifest start_urls
# route installed users straight to /my-cards or /shop/dashboard.


async def test_version_endpoint_returns_short_sha(client):
    """The deploy script polls /version after restart to confirm the
    new uvicorn process actually picked up the new code. Endpoint should
    return JSON with a non-empty `version` string."""
    response = await client.get("/version")
    assert response.status_code == 200
    body = response.json()
    assert "version" in body
    assert isinstance(body["version"], str)
    assert body["version"]  # non-empty


async def test_legacy_register_redirects_to_login(client):
    """/shop/register first hops to shop.taemdee.com (subdomain bouncer)
    then to /shop/login. Follow the chain to assert the legacy
    referral URL still lands on the login page."""
    response = await client.get("/shop/register", follow_redirects=True)
    assert "/shop/login" in str(response.url)


async def test_legacy_register_preserves_ref_query(client):
    response = await client.get(
        "/shop/register?ref=ABC123", follow_redirects=True,
    )
    assert "/shop/login" in str(response.url)
    assert "ref=ABC123" in str(response.url)
