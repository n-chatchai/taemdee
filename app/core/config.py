from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore"
    )

    database_url: str

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


settings = Settings()
