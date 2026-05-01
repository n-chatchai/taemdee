async def test_home_renders_logged_out(client):
    response = await client.get("/")
    assert response.status_code == 200
    body = response.text
    assert 'href="/shop/login"' in body
    assert 'href="/my-cards"' in body
    assert "/static/css/home.css" in body
    # Logged-out users do NOT see the dashboard link in the nav-cta
    assert 'href="/shop/dashboard"' not in body


async def test_home_redirects_logged_in_shop_to_dashboard(auth_client):
    """PWA-installed shops should land in their dashboard, not the marketing
    pitch they've already seen during onboarding."""
    response = await auth_client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/dashboard"


async def test_home_redirects_claimed_customer_to_my_cards(client, db, shop):
    """A customer who has claimed their account lands on /my-cards — their
    actual stamp collection — instead of the marketing pitch."""
    from sqlmodel import select

    from app.models import Customer

    # /scan creates an anonymous customer cookie; flip is_anonymous to simulate
    # a claimed account (skips the OTP+SoftWall flow which is tested elsewhere).
    await client.get(f"/scan/{shop.id}", follow_redirects=True)
    customer = (await db.exec(select(Customer))).first()
    customer.is_anonymous = False
    customer.phone = "0812345678"
    db.add(customer)
    await db.commit()

    response = await client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/my-cards"


async def test_home_redirects_anonymous_customer_to_my_cards(client, shop):
    """Per the revised C7 design, guests see /my-cards too — same list,
    just with the green signup banner pinned at the bottom inviting them
    to convert. No need to bounce guests to a different page."""
    await client.get(f"/scan/{shop.id}", follow_redirects=True)

    response = await client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/my-cards"


async def test_switch_renders_role_picker_unconditionally(auth_client):
    """/switch is the in-app re-entry to the picker — needed because PWA
    standalone mode hides the URL bar so users can't manually visit /."""
    response = await auth_client.get("/switch")
    assert response.status_code == 200
    body = response.text
    assert "role-picker-page" in body
    assert 'href="/shop/dashboard"' in body
    assert 'href="/my-cards"' in body


async def test_settings_pages_link_to_switch(auth_client, named_client):
    """Both shop settings and customer account menu surface a "เปลี่ยนหน้าใช้งาน"
    row pointing at /switch — the only entry point once a PWA icon is pinned."""
    shop_settings = (await auth_client.get("/shop/settings")).text
    assert "เปลี่ยนหน้าใช้งาน" in shop_settings
    assert 'href="/switch"' in shop_settings

    customer_account = (await named_client.get("/card/account")).text
    assert "เปลี่ยนหน้าใช้งาน" in customer_account
    assert 'href="/switch"' in customer_account


async def test_home_renders_role_picker_when_both_sessions_valid(auth_client, shop):
    """When the same device carries a valid shop session AND a valid
    customer session (e.g. an owner who also collects points elsewhere),
    we render a chooser instead of slamming them into /shop/dashboard."""
    # auth_client already has the shop session cookie. /scan layers on a
    # customer cookie for the same browser.
    await auth_client.get(f"/scan/{shop.id}", follow_redirects=True)

    response = await auth_client.get("/", follow_redirects=False)
    assert response.status_code == 200
    body = response.text
    # Picker chrome + both destinations as plain links (no redirect).
    assert "role-picker-page" in body
    assert "เข้าใช้งานเป็น" in body
    assert 'href="/shop/dashboard"' in body
    assert 'href="/my-cards"' in body
    assert ">ร้านค้า<" in body
    assert ">ลูกค้า<" in body


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
    response = await client.get("/shop/register", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/login"


async def test_legacy_register_preserves_ref_query(client):
    response = await client.get("/shop/register?ref=ABC123", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/login?ref=ABC123"
