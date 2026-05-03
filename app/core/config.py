import os
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore"
    )

    database_url: str
    redis_url: str = "redis://localhost:6379"

    # JWT signing key. Must be long + random. Never commit a real value.
    jwt_secret: str
    jwt_algorithm: str = "HS256"

    # How long a session cookie stays valid.
    session_expire_days: int = 30

    # Domains for subdomain separation.
    main_domain: str = "taemdee.com"
    shop_domain: str = "shop.taemdee.com"

    # "development" or "production". Controls cookie Secure flag + SMS sending.
    environment: str = "development"

    # LINE Login (optional — if unset, the LINE button returns 503).
    line_channel_id: Optional[str] = None
    line_channel_secret: Optional[str] = None
    line_redirect_uri: str = "https://taemdee.com/auth/line/callback"
    login_otp_simulate: bool = False

    # Login methods enabled for each role (comma-separated: line,phone,google,facebook)
    customer_logins: str = "line,google"
    shop_logins: str = "line,google"

    # Google OAuth 2.0 (optional — if unset, the Google button returns 503).
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    google_redirect_uri: str = "https://taemdee.com/auth/google/callback"

    # Facebook Login (optional — if unset, the FB button returns 503).
    facebook_app_id: Optional[str] = None
    facebook_app_secret: Optional[str] = None
    facebook_redirect_uri: str = "https://taemdee.com/auth/facebook/callback"

    # Slack incoming-webhook for deploy notifications.
    slack_webhook_url: Optional[str] = None

    # DeeReach: default SMS sender name (if the provider supports it).
    sms_sender: str = "TaemDee"

    # Asset versioning (set by deploy script to bust caches).
    asset_version: str = "dev"

    # --- Helper methods to check if a provider is enabled ---
    def is_login_enabled(self, role: str, provider: str) -> bool:
        """Checks if a provider (line, phone, google, facebook) is enabled for a role."""
        methods_str = self.customer_logins if role == "customer" else self.shop_logins
        enabled_methods = {s.strip().lower() for s in methods_str.split(",")}
        return provider.lower() in enabled_methods

    # Legacy properties for existing templates (will eventually migrate them)
    @property
    def google_login_enabled(self) -> bool:
        return self.is_login_enabled("customer", "google")

    @property
    def facebook_login_enabled(self) -> bool:
        return self.is_login_enabled("customer", "facebook")

    @property
    def phone_login_enabled(self) -> bool:
        return self.is_login_enabled("customer", "phone")


settings = Settings()
