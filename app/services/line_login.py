"""LINE Login OAuth2 flow.

Standard authorization-code flow. CSRF state helpers live in
`oauth_state` so google_login + facebook_login share the same JWT
nonce machinery.
"""

from typing import Optional
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.services.oauth_state import (  # re-exported for backwards compat
    OAUTH_STATE_TTL_MINUTES,
    make_oauth_state,
    verify_oauth_state,
)

LINE_AUTHORIZE_URL = "https://access.line.me/oauth2/v2.1/authorize"
LINE_TOKEN_URL = "https://api.line.me/oauth2/v2.1/token"
LINE_PROFILE_URL = "https://api.line.me/v2/profile"


class LineLoginError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.line_channel_id and settings.line_channel_secret)


__all__ = [
    "LINE_AUTHORIZE_URL",
    "LINE_TOKEN_URL",
    "LINE_PROFILE_URL",
    "LineLoginError",
    "OAUTH_STATE_TTL_MINUTES",
    "build_authorize_url",
    "exchange_code_for_token",
    "fetch_profile",
    "is_configured",
    "make_oauth_state",
    "verify_oauth_state",
]


def build_authorize_url(state: str) -> str:
    """Construct the LINE authorize URL the browser is redirected to."""
    if not is_configured():
        raise LineLoginError("LINE_CHANNEL_ID / LINE_CHANNEL_SECRET not set in .env")
    params = {
        "response_type": "code",
        "client_id": settings.line_channel_id,
        "redirect_uri": settings.line_redirect_uri,
        "state": state,
        "scope": "profile openid",
    }
    return f"{LINE_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(code: str) -> dict:
    """Server-to-server: exchange the authorization code for an access token."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            LINE_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.line_redirect_uri,
                "client_id": settings.line_channel_id,
                "client_secret": settings.line_channel_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code != 200:
        raise LineLoginError(
            f"LINE token exchange failed ({response.status_code}): {response.text}"
        )
    return response.json()


async def fetch_profile(access_token: str) -> dict:
    """Fetch user profile (userId, displayName, pictureUrl, statusMessage)."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            LINE_PROFILE_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code != 200:
        raise LineLoginError(
            f"LINE profile fetch failed ({response.status_code}): {response.text}"
        )
    return response.json()
