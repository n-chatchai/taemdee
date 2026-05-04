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

    # Domain for the platform. Subdomain "shop." is automatically prefixed for shops.
    domain_name: str = "taemdee.com"

    @property
    def main_domain(self) -> str:
        return self.domain_name

    @property
    def shop_domain(self) -> str:
        return f"shop.{self.domain_name}"

    # --- Redirect URIs (automatically derived from domain_name) ---
    @property
    def line_redirect_uri(self) -> str:
        return f"https://{self.domain_name}/auth/line/callback"

    @property
    def google_redirect_uri(self) -> str:
        return f"https://{self.domain_name}/auth/google/callback"

    @property
    def facebook_redirect_uri(self) -> str:
        return f"https://{self.domain_name}/auth/facebook/callback"

    # "development" or "production". Controls cookie Secure flag + SMS sending.
    environment: str = "development"

    # LINE Login (optional — if unset, the LINE button returns 503).
    line_channel_id: Optional[str] = None
    line_channel_secret: Optional[str] = None
    login_otp_simulate: bool = False

    # LINE Messaging API — single platform OA (@taemdee). Login channel
    # and Messaging channel must share the same LINE Provider so the
    # userId captured during LINE Login is interchangeable with the
    # recipient id needed to push messages. When unset, _send_line in
    # tasks/deereach.py falls back to its log-only stub so dev keeps
    # working without real LINE creds.
    line_oa_channel_access_token: Optional[str] = None
    line_oa_channel_secret: Optional[str] = None
    # The OA's friend-link handle, used by the customer-side
    # "เพิ่มเพื่อน @taemdee" prompt. https://line.me/R/ti/p/{basic_id}
    # Take the value with the leading "@" included.
    line_oa_basic_id: str = "@taemdee"

    @property
    def line_messaging_configured(self) -> bool:
        return bool(self.line_oa_channel_access_token and self.line_oa_channel_secret)

    @property
    def line_oa_friend_url(self) -> str:
        return f"https://line.me/R/ti/p/{self.line_oa_basic_id}"

    # Login methods enabled for each role (comma-separated: line,phone,google,facebook).
    # Facebook is disabled until we push the FB app through Meta's
    # business verification — going Live for real users now requires it
    # even for basic public_profile + email. Re-enable by adding
    # "facebook" once FACEBOOK_APP_ID / FACEBOOK_APP_SECRET are set.
    customer_logins: str = "line,google"
    # Shop-side methods. "username" enables the user-level
    # username + 6-digit PIN login (no OAuth round-trip) at
    # /staff/pin-login. Customer side intentionally never includes it
    # — PWA is connect-only, no login wall.
    shop_logins: str = "line,google,username"

    # Google OAuth 2.0 (optional — if unset, the Google button returns 503).
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None

    # Facebook Login (optional — if unset, the FB button returns 503).
    facebook_app_id: Optional[str] = None
    facebook_app_secret: Optional[str] = None

    # Slack incoming-webhook for deploy notifications.
    slack_deploy_webhook_url: Optional[str] = None

    # How many "ลูกค้าล่าสุด" feed rows the S3 dock keeps visible.
    shop_customer_last_scan_display_number: int = 10

    # Welcome-credit grant amount (in CREDITS, not satang)
    credit_welcome_amount: int = 50

    # Cloudflare R2 Storage.
    r2_endpoint_url: Optional[str] = None
    r2_access_key_id: Optional[str] = None
    r2_secret_access_key: Optional[str] = None
    r2_bucket: str = "taemdee"
    r2_public_url: Optional[str] = None

    # DeeReach: default SMS sender name (if the provider supports it).
    sms_sender: str = "TaemDee"

    # Asset versioning (set by deploy script to bust caches).
    asset_version: str = "dev"

    # Slip2Go (PromptPay slip verification). When `slip2go_api_secret`
    # is empty, /shop/topup/upload returns a friendly "verifier not
    # configured" error. `bank_transfer_skip_check` simulates a
    # successful verify in dev so designers can exercise the flow
    # without uploading a real bank slip.
    slip2go_api_secret: Optional[str] = None
    slip2go_success_codes: str = "200000,200200"
    slip2go_success_messages: str = "Slip found,Slip is valid"
    bank_receiver_bank_id: Optional[str] = None       # e.g. "014" for SCB
    bank_receiver_account_suffix: Optional[str] = None
    bank_receiver_name: Optional[str] = None          # comma-separated tokens, any-of match
    bank_transfer_skip_check: bool = False            # dev-only simulation

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
