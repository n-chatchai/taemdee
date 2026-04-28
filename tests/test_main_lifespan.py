"""Lifespan bootstrap helpers — VAPID keypair generation."""

from sqlmodel import select

from app.core.config import settings
from app.main import _ensure_vapid_keys
from app.models import AppSecret


async def test_ensure_vapid_keys_generates_pair_when_missing(db, monkeypatch):
    """First boot — no env, no DB row → generate + persist."""
    monkeypatch.setattr(settings, "web_push_vapid_public_key", None)
    monkeypatch.setattr(settings, "web_push_vapid_private_key", None)

    # Sanity: nothing in app_secrets yet.
    rows = (await db.exec(select(AppSecret))).all()
    assert rows == []

    await _ensure_vapid_keys(db=db)

    # Settings populated.
    assert settings.web_push_vapid_public_key
    assert settings.web_push_vapid_private_key
    assert "BEGIN PRIVATE KEY" in settings.web_push_vapid_private_key

    # Persisted to DB so the next worker boot finds them.
    rows = (await db.exec(select(AppSecret))).all()
    names = {r.name for r in rows}
    assert names == {"web_push_vapid_public", "web_push_vapid_private"}


async def test_ensure_vapid_keys_skips_when_env_set(db, monkeypatch):
    """Operator override via .env — helper must leave settings + DB alone."""
    monkeypatch.setattr(settings, "web_push_vapid_public_key", "ENV_PUB")
    monkeypatch.setattr(settings, "web_push_vapid_private_key", "ENV_PRIV")

    await _ensure_vapid_keys(db=db)

    assert settings.web_push_vapid_public_key == "ENV_PUB"
    assert settings.web_push_vapid_private_key == "ENV_PRIV"
    rows = (await db.exec(select(AppSecret))).all()
    assert rows == []


async def test_ensure_vapid_keys_loads_existing_db_rows(db, monkeypatch):
    """Second boot after a previous generation — load + cache, do not regenerate."""
    monkeypatch.setattr(settings, "web_push_vapid_public_key", None)
    monkeypatch.setattr(settings, "web_push_vapid_private_key", None)

    db.add(AppSecret(name="web_push_vapid_public", value="STORED_PUB"))
    db.add(AppSecret(name="web_push_vapid_private", value="STORED_PRIV_PEM"))
    await db.commit()

    await _ensure_vapid_keys(db=db)

    assert settings.web_push_vapid_public_key == "STORED_PUB"
    assert settings.web_push_vapid_private_key == "STORED_PRIV_PEM"
    # Still only the two rows we seeded — no duplicates.
    rows = (await db.exec(select(AppSecret))).all()
    assert len(rows) == 2
