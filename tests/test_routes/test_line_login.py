"""LINE Login route tests — only the configured / unconfigured guard.

Full callback flow needs httpx mocking against api.line.me; deferred until needed.
"""

from app.core.config import settings


async def test_line_start_unconfigured_returns_503(client):
    response = await client.get("/auth/line/start", follow_redirects=False)
    assert response.status_code == 503


async def test_line_start_configured_redirects_to_line(client, monkeypatch):
    monkeypatch.setattr(settings, "line_channel_id", "1234567890")
    monkeypatch.setattr(settings, "line_channel_secret", "secret")

    response = await client.get("/auth/line/start", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://access.line.me/oauth2/v2.1/authorize?")
    # The state cookie was set
    assert "line_oauth_state" in response.cookies


async def test_line_callback_bad_state_400(client):
    response = await client.get(
        "/auth/line/callback?code=abc&state=nonsense",
        follow_redirects=False,
    )
    assert response.status_code == 400
