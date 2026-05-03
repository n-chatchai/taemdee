import pytest
from app.core.config import Settings

def test_is_login_enabled_defaults():
    settings = Settings(database_url="sqlite:///:memory:", jwt_secret="test")
    # Defaults should be line,google for both
    assert settings.is_login_enabled("customer", "line") is True
    assert settings.is_login_enabled("customer", "google") is True
    assert settings.is_login_enabled("customer", "phone") is False
    assert settings.is_login_enabled("customer", "facebook") is False
    
    assert settings.is_login_enabled("shop", "line") is True
    assert settings.is_login_enabled("shop", "google") is True
    assert settings.is_login_enabled("shop", "phone") is False

def test_is_login_enabled_custom_strings():
    settings = Settings(
        database_url="sqlite:///:memory:", 
        jwt_secret="test",
        customer_logins="phone, facebook ",
        shop_logins="line"
    )
    # Customer: phone, facebook
    assert settings.is_login_enabled("customer", "phone") is True
    assert settings.is_login_enabled("customer", "facebook") is True
    assert settings.is_login_enabled("customer", "line") is False
    assert settings.is_login_enabled("customer", "google") is False
    
    # Shop: line
    assert settings.is_login_enabled("shop", "line") is True
    assert settings.is_login_enabled("shop", "phone") is False

def test_is_login_enabled_case_and_whitespace():
    settings = Settings(
        database_url="sqlite:///:memory:", 
        jwt_secret="test",
        customer_logins=" LINE , GOOGLE "
    )
    assert settings.is_login_enabled("customer", "line") is True
    assert settings.is_login_enabled("customer", "google") is True
    assert settings.is_login_enabled("customer", "LINE") is True

def test_legacy_properties():
    settings = Settings(
        database_url="sqlite:///:memory:", 
        jwt_secret="test",
        customer_logins="phone"
    )
    assert settings.phone_login_enabled is True
    assert settings.google_login_enabled is False
    assert settings.facebook_login_enabled is False
