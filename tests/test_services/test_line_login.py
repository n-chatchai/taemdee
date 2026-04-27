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
    monkeypatch.setattr(settings, "line_redirect_uri", "http://x/cb")

    url = build_authorize_url("nonce-xyz")
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    assert parsed.netloc == "access.line.me"
    assert parsed.path == "/oauth2/v2.1/authorize"
    assert qs["client_id"] == ["1234567890"]
    assert qs["redirect_uri"] == ["http://x/cb"]
    assert qs["response_type"] == ["code"]
    assert qs["state"] == ["nonce-xyz"]
    assert "profile" in qs["scope"][0]


def test_state_round_trip_defaults_to_shop_role():
    nonce, cookie = make_oauth_state()
    assert verify_oauth_state(nonce, cookie) == "shop"


def test_state_round_trip_carries_customer_role():
    nonce, cookie = make_oauth_state(role="customer")
    assert verify_oauth_state(nonce, cookie) == "customer"


def test_state_mismatch_rejected():
    nonce, cookie = make_oauth_state()
    assert verify_oauth_state("different-nonce", cookie) is None


def test_state_no_cookie_rejected():
    nonce, _ = make_oauth_state()
    assert verify_oauth_state(nonce, None) is None
    assert verify_oauth_state(nonce, "") is None


def test_state_garbage_cookie_rejected():
    assert verify_oauth_state("anything", "not-a-jwt") is None
