"""Google OAuth route tests — guard + state-failure paths.

Full callback flow needs httpx mocking against accounts.google.com /
openidconnect.googleapis.com; deferred until needed.
"""

from app.core.config import settings


async def test_google_customer_start_unconfigured_returns_503(client):
    response = await client.get("/auth/google/customer/start", follow_redirects=False)
    assert response.status_code == 503


async def test_google_customer_start_configured_redirects(client, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "fake-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "fake-secret")

    response = await client.get("/auth/google/customer/start", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "google_oauth_state" in response.cookies


async def test_google_callback_bad_state_400(client):
    response = await client.get(
        "/auth/google/callback?code=abc&state=nonsense",
        follow_redirects=False,
    )
    assert response.status_code == 400
