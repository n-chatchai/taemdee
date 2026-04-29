"""Web Push (VAPID) helpers.

Keys live exclusively in the `app_secrets` table — no .env override.
The RQ worker generates the keypair on first boot via `ensure_vapid_keys`;
the web process just reads them via `load_vapid_keys` (read-only,
process-cached). Other modules pull from `get_vapid_public_key()` /
`get_vapid_private_key()`.

Worker is the only generator on purpose: VAPID setup needs DB write
permission, and we'd rather have one process responsible than race
gunicorn workers + the RQ worker on every fresh deploy.
"""

import base64
import logging
from typing import Optional, Tuple

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import engine
from app.models import AppSecret

log = logging.getLogger(__name__)

PUB_KEY_NAME = "web_push_vapid_public"
PRIV_KEY_NAME = "web_push_vapid_private"

# VAPID `sub` field — push services contact this address on abuse /
# rate-limit issues. Channel-specific mailbox so it doesn't get drowned
# in generic contact@ traffic.
WEB_PUSH_VAPID_SUB = "mailto:push@taemdee.com"

# Process-local cache. Each gunicorn worker + the RQ worker keep their
# own copy after the first DB read; subsequent calls are free. Cleared
# only by process restart.
_cache: dict[str, Optional[str]] = {"public": None, "private": None}


async def _read_keys(session: AsyncSession) -> Tuple[Optional[str], Optional[str]]:
    rows = (await session.exec(
        select(AppSecret).where(AppSecret.name.in_([PUB_KEY_NAME, PRIV_KEY_NAME]))
    )).all()
    by_name = {r.name: r.value for r in rows}
    return by_name.get(PUB_KEY_NAME), by_name.get(PRIV_KEY_NAME)


def _populate_cache(public: str, private: str) -> None:
    _cache["public"] = public
    _cache["private"] = private


async def load_vapid_keys(db: AsyncSession) -> None:
    """Read-only — populate the in-process cache from app_secrets, never
    generate. Web routes call this so a missing keypair surfaces as a
    503 rather than a silent generate-on-the-hot-path that races the
    worker. Idempotent."""
    if _cache["public"] and _cache["private"]:
        return
    pub, priv = await _read_keys(db)
    if pub and priv:
        _populate_cache(pub, priv)


async def ensure_vapid_keys(db: Optional[AsyncSession] = None) -> None:
    """Worker-only — read existing keys, generate + save if absent, then
    populate the cache. Race-safe: first INSERT wins, the loser swallows
    IntegrityError and re-reads.

    `db` lets tests inject a session; production passes None so we open
    a fresh one on the global engine."""
    if _cache["public"] and _cache["private"]:
        return

    async def _do(session: AsyncSession) -> None:
        pub, priv = await _read_keys(session)
        if pub and priv:
            _populate_cache(pub, priv)
            return

        # Fresh ECDSA P-256 pair. Public key in X9.62 uncompressed form,
        # base64url-encoded (browser pushManager.subscribe format);
        # private key as PKCS#8 PEM (pywebpush vapid_private_key format).
        v = Vapid01()
        v.generate_keys()
        pub_b64 = base64.urlsafe_b64encode(
            v.public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
        ).decode().rstrip("=")
        priv_pem = v.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()

        session.add(AppSecret(name=PUB_KEY_NAME, value=pub_b64))
        session.add(AppSecret(name=PRIV_KEY_NAME, value=priv_pem))
        try:
            await session.commit()
            log.info("Generated VAPID keypair (web_push) — stored in app_secrets")
            _populate_cache(pub_b64, priv_pem)
        except IntegrityError:
            await session.rollback()
            pub2, priv2 = await _read_keys(session)
            if pub2 and priv2:
                _populate_cache(pub2, priv2)

    if db is not None:
        await _do(db)
    else:
        async with AsyncSession(engine) as session:
            await _do(session)


def get_vapid_public_key() -> Optional[str]:
    """Cached public key (None until load_vapid_keys / ensure_vapid_keys
    has run in this process)."""
    return _cache["public"]


def get_vapid_private_key() -> Optional[str]:
    """Cached private key (None until ensure_vapid_keys has run in this
    process — i.e. only the worker holds this)."""
    return _cache["private"]
