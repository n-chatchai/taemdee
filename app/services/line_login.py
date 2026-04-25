"""LINE Login OAuth2 flow.

Standard authorization-code flow. We use a CSRF-protected `state` parameter:
the URL-state is a random nonce, the same nonce is signed and stored in a
short-lived cookie, and the callback verifies they match.
"""

import secrets
from datetime import timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt

from app.core.config import settings
from app.models.util import utcnow

LINE_AUTHORIZE_URL = "https://access.line.me/oauth2/v2.1/authorize"
LINE_TOKEN_URL = "https://api.line.me/oauth2/v2.1/token"
LINE_PROFILE_URL = "https://api.line.me/v2/profile"

OAUTH_STATE_TTL_MINUTES = 10


class LineLoginError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.line_channel_id and settings.line_channel_secret)


def make_oauth_state() -> tuple[str, str]:
    """Returns (url_state, cookie_token).

    url_state goes into the LINE authorize URL.
    cookie_token is a signed JWT containing the same nonce — stored in an httpOnly cookie.
    On callback we verify the cookie token AND that its nonce matches the URL state.
    """
    nonce = secrets.token_urlsafe(24)
    cookie_token = jwt.encode(
        {"nonce": nonce, "exp": utcnow() + timedelta(minutes=OAUTH_STATE_TTL_MINUTES)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return nonce, cookie_token


def verify_oauth_state(url_state: str, cookie_token: Optional[str]) -> bool:
    if not cookie_token:
        return False
    try:
        payload = jwt.decode(
            cookie_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return False
    return payload.get("nonce") == url_state


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
