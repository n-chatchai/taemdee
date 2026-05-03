import pytest
from app.core.config import settings
from app.core.auth import SESSION_COOKIE_NAME, CUSTOMER_COOKIE_NAME

@pytest.mark.asyncio
async def test_session_auth_error_redirects_to_shop_login(client):
    """
    Test that hitting a protected shop route without a session 
    redirects to /shop/login on the shop domain.
    """
    headers = {"Host": settings.shop_domain, "Accept": "text/html"}
    response = await client.get("/shop/dashboard", headers=headers, follow_redirects=False)
    
    assert response.status_code == 303
    assert "/shop/login" in response.headers["location"]
    assert "reason=session_missing" in response.headers["location"]

@pytest.mark.asyncio
async def test_customer_auth_error_redirects_to_customer_login(client):
    """
    Test that hitting a customer route with an INVALID cookie
    redirects to /customer/login on the main domain.
    """
    # Send a garbage cookie that will fail decoding
    # Ensure domain matches or is not set so httpx sends it for the Host
    client.cookies.set(CUSTOMER_COOKIE_NAME, "garbage-token")
    
    headers = {"Host": "taemdee.com", "Accept": "text/html"}
    response = await client.get("/my-cards", headers=headers, follow_redirects=False)
    
    assert response.status_code == 303
    assert "/customer/login" in response.headers["location"]
    assert "reason=token_invalid" in response.headers["location"]
    # Check that the bad cookie is cleared
    assert CUSTOMER_COOKIE_NAME in response.headers.get("set-cookie", "")
    assert 'expires=' in response.headers.get("set-cookie", "")

@pytest.mark.asyncio
async def test_api_auth_error_returns_401_no_redirect(client):
    """
    Test that JSON/API requests get a 401 instead of a 303 redirect.
    """
    headers = {"Host": settings.shop_domain, "Accept": "application/json"}
    response = await client.get("/shop/dashboard", headers=headers, follow_redirects=False)
    
    assert response.status_code == 401
    assert response.json()["detail"] == "ยังไม่ได้เข้าสู่ระบบ — กรุณาเข้าสู่ระบบเพื่อใช้แดชบอร์ด"
