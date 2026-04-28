from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.util import utcnow


class AppSecret(SQLModel, table=True):
    """App-wide singleton secrets keyed by name. Used for values that should
    survive deploys but don't belong in version-controlled .env (auto-
    generated VAPID keys, future encryption salts, etc.). Lifespan
    bootstraps reads from here first, falls back to env, generates + stores
    if both are empty."""

    __tablename__ = "app_secrets"

    name: str = Field(primary_key=True)
    value: str
    created_at: datetime = Field(default_factory=utcnow)
