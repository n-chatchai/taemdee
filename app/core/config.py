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

    # "development" or "production". Controls cookie Secure flag + SMS sending.
    environment: str = "development"

    # LINE Login (optional — if unset, the LINE button returns 503).
    line_channel_id: Optional[str] = None
    line_channel_secret: Optional[str] = None
    line_redirect_uri: str = "https://taemdee.com/auth/line/callback"
    login_otp_simulate: bool = False

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
    # used for PUTs only — it serves zero public traffic. R2_PUBLIC_URL is the
    # browser-facing host (R2.dev subdomain or custom domain) the uploaded URL
    # is built from. If R2_PUBLIC_URL is empty we fall back to the API host,
    # which won't actually serve images publicly — set it before going live.
    r2_endpoint_url: Optional[str] = None
    r2_access_key_id: Optional[str] = None
    r2_secret_access_key: Optional[str] = None
    r2_bucket: str = "taemdee"
    r2_public_url: Optional[str] = None


settings = Settings()
