"""Pure-logic tests for the LINE Login service. Network calls aren't covered."""

from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import settings
from app.services.line_login import (
    LineLoginError,
    build_authorize_url,
    is_configured,
    make_oauth_state,
    verify_oauth_state,
)


def test_unconfigured_by_default():
    """No LINE creds in .env.example → is_configured() is False."""
    assert is_configured() is False


def test_build_authorize_url_raises_when_unconfigured():
    with pytest.raises(LineLoginError, match="LINE_CHANNEL"):
        build_authorize_url("abc")


def test_build_authorize_url_with_creds(monkeypatch):
    monkeypatch.setattr(settings, "line_channel_id", "1234567890")
    monkeypatch.setattr(settings, "line_channel_secret", "secret")
    monkeypatch.setattr(settings, "domain_name", "x")

    url = build_authorize_url("nonce-xyz")
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    assert parsed.netloc == "access.line.me"
    assert parsed.path == "/oauth2/v2.1/authorize"
    assert qs["client_id"] == ["1234567890"]
    assert qs["redirect_uri"] == ["https://x/auth/line/callback"]
    assert qs["response_type"] == ["code"]
    assert qs["state"] == ["nonce-xyz"]
    assert "profile" in qs["scope"][0]


# `make_oauth_state` now returns a single signed JWT instead of a
# (nonce, cookie) pair — the cookie-bound flow was retired in favour
# of stateless verification. The callers pass `state` directly.


def test_state_round_trip_defaults_to_shop_role():
    state = make_oauth_state()
    payload = verify_oauth_state(state)
    assert payload is not None
    assert payload["role"] == "shop"
    assert "next_redeem" not in payload


def test_state_round_trip_carries_customer_role():
    state = make_oauth_state(role="customer")
    payload = verify_oauth_state(state)
    assert payload is not None
    assert payload["role"] == "customer"


def test_state_round_trip_carries_next_redeem():
    state = make_oauth_state(role="customer", next_redeem="abc-123")
    payload = verify_oauth_state(state)
    assert payload is not None
    assert payload["next_redeem"] == "abc-123"


def test_state_garbage_rejected():
    assert verify_oauth_state("not-a-jwt") is None


def test_state_empty_rejected():
    assert verify_oauth_state("") is None
