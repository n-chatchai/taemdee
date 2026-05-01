"""Facebook OAuth route tests — guard + state-failure paths."""

from app.core.config import settings


async def test_facebook_customer_start_unconfigured_returns_503(client):
    response = await client.get("/auth/facebook/customer/start", follow_redirects=False)
    assert response.status_code == 503


async def test_facebook_customer_start_configured_redirects(client, monkeypatch):
    monkeypatch.setattr(settings, "facebook_app_id", "1234567890")
    monkeypatch.setattr(settings, "facebook_app_secret", "fake-secret")

    response = await client.get("/auth/facebook/customer/start", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://www.facebook.com/v22.0/dialog/oauth?")
    assert "facebook_oauth_state" in response.cookies


async def test_facebook_callback_bad_state_400(client):
    response = await client.get(
        "/auth/facebook/callback?code=abc&state=nonsense",
        follow_redirects=False,
    )
    assert response.status_code == 400
