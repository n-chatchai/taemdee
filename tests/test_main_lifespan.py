"""VAPID key bootstrap — generate-or-load helper used by the RQ worker."""

from sqlmodel import select

from app.models import AppSecret
from app.services.web_push import (
    PRIV_KEY_NAME,
    PUB_KEY_NAME,
    ensure_vapid_keys,
    get_vapid_private_key,
    get_vapid_public_key,
    load_vapid_keys,
    _cache,
)


async def test_ensure_vapid_keys_generates_pair_when_missing(db, monkeypatch):
    """First boot — no DB row → generate, persist, populate cache."""
    monkeypatch.setitem(_cache, "public", None)
    monkeypatch.setitem(_cache, "private", None)

    rows = (await db.exec(select(AppSecret))).all()
    assert rows == []

    await ensure_vapid_keys(db=db)

    pub = get_vapid_public_key()
    priv = get_vapid_private_key()
    assert pub
    assert priv and "BEGIN PRIVATE KEY" in priv

    rows = (await db.exec(select(AppSecret))).all()
    names = {r.name for r in rows}
    assert names == {PUB_KEY_NAME, PRIV_KEY_NAME}


async def test_ensure_vapid_keys_loads_existing_db_rows(db, monkeypatch):
    """Second boot after a previous generation — load + cache, no regenerate."""
    monkeypatch.setitem(_cache, "public", None)
    monkeypatch.setitem(_cache, "private", None)

    db.add(AppSecret(name=PUB_KEY_NAME, value="STORED_PUB"))
    db.add(AppSecret(name=PRIV_KEY_NAME, value="STORED_PRIV_PEM"))
    await db.commit()

    await ensure_vapid_keys(db=db)
    assert get_vapid_public_key() == "STORED_PUB"
    assert get_vapid_private_key() == "STORED_PRIV_PEM"

    rows = (await db.exec(select(AppSecret))).all()
    assert len(rows) == 2  # no duplicates


async def test_load_vapid_keys_does_not_generate(db, monkeypatch):
    """Web-side helper must stay read-only: empty DB → cache stays empty,
    no row inserted. Worker is the only generator."""
    monkeypatch.setitem(_cache, "public", None)
    monkeypatch.setitem(_cache, "private", None)

    await load_vapid_keys(db)
    assert get_vapid_public_key() is None
    assert get_vapid_private_key() is None

    rows = (await db.exec(select(AppSecret))).all()
    assert rows == []


async def test_load_vapid_keys_populates_cache_when_db_has_rows(db, monkeypatch):
    monkeypatch.setitem(_cache, "public", None)
    monkeypatch.setitem(_cache, "private", None)

    db.add(AppSecret(name=PUB_KEY_NAME, value="PUB"))
    db.add(AppSecret(name=PRIV_KEY_NAME, value="PRIV_PEM"))
    await db.commit()

    await load_vapid_keys(db)
    assert get_vapid_public_key() == "PUB"
    assert get_vapid_private_key() == "PRIV_PEM"
