"""Shared CSRF state helpers for OAuth2 authorization-code flows.

Pattern: a random nonce travels in the URL `state` parameter, the same nonce
plus role + optional next_redeem ride a short-lived signed cookie set on
the start request. The callback verifies the cookie's signature and that
its embedded nonce matches the URL state, then reads the payload to decide
where to land.

Used by line_login, google_login and facebook_login. Each provider keeps
its own state cookie name + cookie path so callbacks don't see each
other's state.
"""

import secrets
from datetime import timedelta
from typing import Optional
from loguru import logger


from jose import JWTError, jwt

from app.core.config import settings
from app.models.util import utcnow

OAUTH_STATE_TTL_MINUTES = 10



def make_oauth_state(
    role: str = "shop", next_redeem: Optional[str] = None
) -> tuple[str, str]:
    """Returns (url_state, cookie_token).

    `url_state` is a random nonce that travels in the URL `state` parameter
    (visible to the OAuth provider). `cookie_token` is a signed JWT that
    embeds the same nonce plus the role tag and an optional next_redeem
    shop id. The caller stores the cookie token on the start response and
    the callback recovers role + next_redeem after verification.
    """
    nonce = secrets.token_urlsafe(24)
    payload = {
        "nonce": nonce,
        "role": role,
        "exp": utcnow() + timedelta(minutes=OAUTH_STATE_TTL_MINUTES),
    }
    if next_redeem:
        payload["next_redeem"] = next_redeem
    cookie_token = jwt.encode(
        payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    return nonce, cookie_token


def verify_oauth_state(url_state: str, cookie_token: Optional[str]) -> Optional[dict]:
    """Returns the JWT payload dict on success, None on failure.

    Callers read `payload["role"]` (always present, defaults to "shop")
    and `payload.get("next_redeem")` (optional shop id).
    """
    if not cookie_token:
        return None
    try:
        payload = jwt.decode(
            cookie_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except Exception as e:
        logger.error(f"❌ JWT decode error: {e}")
        return None
    if payload.get("nonce") != url_state:
        logger.error("❌ Nonce mismatch")
        return None
    payload.setdefault("role", "shop")
    return payload
