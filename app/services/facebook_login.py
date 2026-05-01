"""Facebook Login OAuth2 flow.

Authorization-code flow against Graph API v22. Asks for `email` +
`public_profile` so we can recover an email if the FB account exposes
one (used to merge with an existing phone-claimed Customer that shares
the address). The userinfo response keys are `id`, `name`, `email`.
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

FACEBOOK_GRAPH_VERSION = "v22.0"
FACEBOOK_AUTHORIZE_URL = (
    f"https://www.facebook.com/{FACEBOOK_GRAPH_VERSION}/dialog/oauth"
)
FACEBOOK_TOKEN_URL = (
    f"https://graph.facebook.com/{FACEBOOK_GRAPH_VERSION}/oauth/access_token"
)
FACEBOOK_PROFILE_URL = f"https://graph.facebook.com/{FACEBOOK_GRAPH_VERSION}/me"


class FacebookLoginError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.facebook_app_id and settings.facebook_app_secret)


def build_authorize_url(state: str) -> str:
    if not is_configured():
        raise FacebookLoginError(
            "FACEBOOK_APP_ID / FACEBOOK_APP_SECRET not set in .env"
        )
    params = {
        "response_type": "code",
        "client_id": settings.facebook_app_id,
        "redirect_uri": settings.facebook_redirect_uri,
        "state": state,
        "scope": "email,public_profile",
    }
    return f"{FACEBOOK_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(code: str) -> dict:
    """Facebook returns access_token via GET, not POST — Graph quirk."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            FACEBOOK_TOKEN_URL,
            params={
                "code": code,
                "redirect_uri": settings.facebook_redirect_uri,
                "client_id": settings.facebook_app_id,
                "client_secret": settings.facebook_app_secret,
            },
        )
    if response.status_code != 200:
        raise FacebookLoginError(
            f"Facebook token exchange failed ({response.status_code}): {response.text}"
        )
    return response.json()


async def fetch_profile(access_token: str) -> dict:
    """Graph /me. We request id, name, email explicitly — Graph defaults
    to id+name only otherwise. email may be missing if the user denied
    the email permission at consent — callers must handle None."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            FACEBOOK_PROFILE_URL,
            params={"fields": "id,name,email"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code != 200:
        raise FacebookLoginError(
            f"Facebook profile fetch failed ({response.status_code}): {response.text}"
        )
    return response.json()
