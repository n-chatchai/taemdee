"""Shared CSRF state helpers for OAuth2 authorization-code flows.

Pattern: a signed JWT (nonce + role + optional next_redeem + exp) travels
as the URL `state` parameter — the only thing the provider sees and
echoes back. Verification decodes the JWT and trusts its signature; no
companion cookie is needed.

Why no cookie: PWAs (especially iOS Safari home-screen apps) often
redirect OAuth out to the system browser, where the cookie store is
separate from the PWA's web view. The cookie set on the start request
isn't visible to the callback, so cookie-bound state verification
fails with "Invalid OAuth state" even when the user authenticated
correctly. Putting the same payload in a signed URL parameter
sidesteps the cross-context cookie problem entirely — JWTs are
tamper-proof, so the "state" round-trip is still CSRF-safe.

Used by line_login, google_login and facebook_login.
"""

from datetime import timedelta
from typing import Optional

from jose import JWTError, jwt
from loguru import logger

from app.core.config import settings
from app.models.util import utcnow

OAUTH_STATE_TTL_MINUTES = 10


def make_oauth_state(
    role: str = "shop",
    next_redeem: Optional[str] = None,
    connect_customer_id: Optional[str] = None,
) -> str:
    """Returns the signed JWT to pass as the OAuth `state` URL parameter.

    Carries role + optional next_redeem + optional connect_customer_id
    (the customer being connected, baked in at /start so the callback
    binds onto the SAME user no matter what cookie context the
    redirect chain ends up in).
    """
    payload = {
        "role": role,
        "exp": utcnow() + timedelta(minutes=OAUTH_STATE_TTL_MINUTES),
    }
    if next_redeem:
        payload["next_redeem"] = next_redeem
    if connect_customer_id:
        payload["connect_customer_id"] = connect_customer_id
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_oauth_state(url_state: str, _legacy_cookie_token: Optional[str] = None) -> Optional[dict]:
    """Returns the JWT payload dict on success, None on failure.

    Callers read `payload["role"]` (always present, defaults to "shop")
    and `payload.get("next_redeem")` (optional shop id).

    The second positional argument is kept for back-compat with the
    cookie-bound callsites in routes/auth.py — it's ignored. Routes
    can drop the cookie param at their leisure.
    """
    if not url_state:
        return None
    try:
        payload = jwt.decode(
            url_state, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError as e:
        logger.error(f"❌ OAuth state JWT decode error: {e}")
        return None
    payload.setdefault("role", "shop")
    return payload
