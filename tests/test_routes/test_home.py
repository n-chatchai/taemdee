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


async def test_legacy_register_redirects_to_login(client):
    response = await client.get("/shop/register", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/login"


async def test_legacy_register_preserves_ref_query(client):
    response = await client.get("/shop/register?ref=ABC123", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/login?ref=ABC123"
