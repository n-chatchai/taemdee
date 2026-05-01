"""Google OAuth 2.0 (OpenID Connect) flow.

Same authorization-code shape as line_login, the only differences are
endpoint URLs, the scope (`openid email profile`) and the userinfo
response shape (`sub`, `email`, `name`, `picture`).
"""

from typing import Optional
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.services.oauth_state import (  # re-exported
    OAUTH_STATE_TTL_MINUTES,
    make_oauth_state,
    verify_oauth_state,
)

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleLoginError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def build_authorize_url(state: str) -> str:
    if not is_configured():
        raise GoogleLoginError(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set in .env"
        )
    params = {
        "response_type": "code",
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "state": state,
        "scope": "openid email profile",
        # Force the account-chooser even when the browser already has a
        # Google session — otherwise a returning user can't switch
        # accounts on this device.
        "prompt": "select_account",
        # Don't ask for offline access; we only need the userinfo
        # endpoint at sign-in time.
        "access_type": "online",
    }
    return f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(code: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.google_redirect_uri,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code != 200:
        raise GoogleLoginError(
            f"Google token exchange failed ({response.status_code}): {response.text}"
        )
    return response.json()


async def fetch_profile(access_token: str) -> dict:
    """OIDC userinfo. Keys we care about: sub, email, name, picture."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code != 200:
        raise GoogleLoginError(
            f"Google userinfo fetch failed ({response.status_code}): {response.text}"
        )
    return response.json()
