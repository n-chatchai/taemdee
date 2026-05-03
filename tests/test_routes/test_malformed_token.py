import pytest
from app.core.config import settings
from app.core.auth import SESSION_COOKIE_NAME
from jose import jwt
from app.models.util import utcnow
from datetime import timedelta

@pytest.mark.asyncio
async def test_malformed_uuid_in_token_redirects(client):
    """
    Test that a valid JWT but with a malformed shop_id UUID 
    redirects to /shop/login instead of raising a 500.
    """
    # Create a valid JWT with a bad UUID string
    expire = utcnow() + timedelta(days=1)
    payload = {
        "shop_id": "not-a-uuid",
        "staff_id": None,
        "role": "owner",
        "exp": expire,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    
    client.cookies.set(SESSION_COOKIE_NAME, token)
    
    headers = {"Host": settings.shop_domain, "Accept": "text/html"}
    response = await client.get("/shop/dashboard", headers=headers, follow_redirects=False)
    
    assert response.status_code == 303
    assert "/shop/login" in response.headers["location"]
    assert "reason=session_invalid" in response.headers["location"]
