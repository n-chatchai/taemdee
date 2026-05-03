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
    phone_login_enabled: bool = False

    # Google OAuth 2.0 (optional — if unset, the Google button returns 503).
    # Get the client id/secret from Google Cloud Console → APIs & Services →
    # Credentials → "Create OAuth client ID" (Web application). The redirect
    # URI must match the one registered there exactly.
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    google_redirect_uri: str = "https://taemdee.com/auth/google/callback"
    google_login_enabled: bool = False

    # Facebook Login (optional — if unset, the FB button returns 503).
    # App ID + App Secret from Meta for Developers → My Apps → Settings → Basic.
    # Redirect URI must be added under Facebook Login → Settings → Valid OAuth
    # Redirect URIs.
    facebook_app_id: Optional[str] = None
    facebook_app_secret: Optional[str] = None
    facebook_redirect_uri: str = "https://taemdee.com/auth/facebook/callback"
    facebook_login_enabled: bool = False

    # Slack incoming-webhook for deploy notifications. Read by scripts/deploy.sh
    # via the same env var name; surfaced here so it lives in the same .env
    # registry as everything else and is documented next to its peers.
    slack_deploy_webhook_url: Optional[str] = None

    # How many "ลูกค้าล่าสุด" feed rows the S3 dock keeps visible. The route
    # passes this into the dashboard template and the SSE handler trims old
    # rows past this cap as new events arrive. Default 3 matches the original
    # design; bump to 5/10 if a busy shop wants more history at a glance.
    shop_customer_last_scan_display_number: int = 10

    # Welcome-credit grant amount (in CREDITS, not satang) — handed to
    # every shop the first time they tap the dashboard's 'รับเครดิตต้อนรับ'
    # item. 0 disables the item. Stored as credits because the value is
    # shop-facing — converted to satang internally when applied.
    credit_welcome_amount: int = 50

    # Cloudflare R2 Storage. Leave R2_ENDPOINT_URL empty to disable uploads.
    # R2_ENDPOINT_URL is the S3 API host (https://<account>.r2.cloudflarestorage.com)
    # used for signed PUTs only. R2_PUBLIC_URL is the browser-facing host —
    # the "Public Development URL" (pub-xxxx.r2.dev) you toggle on in the R2
    # dashboard, or a custom domain. The API host returns
    # `InvalidArgument: Authorization` to unsigned browser GETs, so the URL
    # we persist must come from R2_PUBLIC_URL, not R2_ENDPOINT_URL.
    r2_endpoint_url: Optional[str] = None
    r2_access_key_id: Optional[str] = None
    r2_secret_access_key: Optional[str] = None
    r2_bucket: str = "taemdee"
    r2_public_url: Optional[str] = None


settings = Settings()
