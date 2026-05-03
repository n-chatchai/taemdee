import pytest
from unittest.mock import MagicMock
from app.core.config import settings
from app.routes import auth

@pytest.mark.asyncio
async def test_line_callback_dispatcher_bounces_to_shop(client, monkeypatch):
    """
    Test that when a shop login callback lands on the main domain, 
    it redirects to the shop subdomain.
    """
    # 1. Mock verify_oauth_state to simulate a valid shop login state
    monkeypatch.setattr(auth, "verify_oauth_state", lambda state, cookie: {"role": "shop"})
    
    # 2. Mock the Host header to be the main domain
    headers = {"Host": settings.main_domain}
    
    # 3. Call the callback on the main domain
    response = await client.get(
        "/auth/line/callback?code=fake_code&state=fake_state",
        headers=headers,
        follow_redirects=False
    )
    
    # 4. Assert it redirects to shop domain
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"https://{settings.shop_domain}/auth/line/callback")
    assert "code=fake_code" in response.headers["location"]
    assert "state=fake_state" in response.headers["location"]

@pytest.mark.asyncio
async def test_line_callback_dispatcher_proceeds_on_shop_domain(client, monkeypatch):
    """
    Test that when a shop login callback lands on the shop domain,
    it proceeds to exchange the token instead of redirecting again.
    """
    # 1. Mock verify_oauth_state
    monkeypatch.setattr(auth, "verify_oauth_state", lambda state, cookie: {"role": "shop"})
    
    # 2. Mock exchange_code_for_token to avoid real network calls
    mock_exchange = MagicMock()
    # Using a simple async wrapper because exchange_code_for_token is awaited
    async def fake_exchange(*args, **kwargs):
        return {"access_token": "fake_token"}
    monkeypatch.setattr(auth, "exchange_code_for_token", fake_exchange)
    
    # 3. Mock fetch_profile
    async def fake_profile(*args, **kwargs):
        return {"userId": "line_123", "displayName": "Test Shop"}
    monkeypatch.setattr(auth, "fetch_profile", fake_profile)
    
    # 4. Mock the Host header to be the shop domain
    headers = {"Host": settings.shop_domain}
    
    # 5. Call the callback on the shop domain
    response = await client.get(
        "/auth/line/callback?code=fake_code&state=fake_state",
        headers=headers,
        follow_redirects=False
    )
    
    # 6. Assert it DOES NOT redirect to shop domain again (it should redirect to dashboard or set cookies)
    # Since we didn't mock the DB part fully, it might fail later, but the 303 to /shop/dashboard 
    # indicates it passed the dispatcher logic.
    assert response.status_code == 303
    assert "/shop/dashboard" in response.headers["location"]
    assert "session" in response.cookies

@pytest.mark.asyncio
async def test_line_callback_customer_proceeds_on_main_domain(client, monkeypatch):
    """
    Test that when a customer login callback lands on the main domain,
    it proceeds normally without bouncing to the shop domain.
    """
    # 1. Mock verify_oauth_state as customer
    monkeypatch.setattr(auth, "verify_oauth_state", lambda state, cookie: {"role": "customer"})
    
    # 2. Mock token exchange and profile
    async def fake_exchange(*args, **kwargs):
        return {"access_token": "fake_token"}
    monkeypatch.setattr(auth, "exchange_code_for_token", fake_exchange)
    
    async def fake_profile(*args, **kwargs):
        return {"userId": "line_cust_123", "displayName": "Test Customer"}
    monkeypatch.setattr(auth, "fetch_profile", fake_profile)
    
    # 3. Host is main domain
    headers = {"Host": settings.main_domain}
    
    # 4. Call callback
    response = await client.get(
        "/auth/line/callback?code=fake_code&state=fake_state",
        headers=headers,
        follow_redirects=False
    )
    
    # 5. Assert it proceeds to customer confirmation (C3.line)
    # Status should be 303 to /auth/line/customer/confirm
    assert response.status_code == 303
    assert "/auth/line/customer/confirm" in response.headers["location"]
    assert "customer" in response.cookies
