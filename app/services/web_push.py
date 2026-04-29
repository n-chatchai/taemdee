"""Web Push (VAPID) helpers — bootstrap keypair, persist to app_secrets.

Both the FastAPI lifespan (in app.main) and the RQ worker boot (in
worker.py) call `ensure_vapid_keys` at startup so each process has the
shared keypair loaded into settings before serving requests / picking
jobs. Without this on the worker, `_send_web_push` falls through to its
log-only stub even when keys exist in the DB — campaigns 'deliver'
without anyone receiving a notification.
"""

import base64
import logging
from typing import Optional

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.database import engine
from app.models import AppSecret

log = logging.getLogger(__name__)

PUB_KEY_NAME = "web_push_vapid_public"
PRIV_KEY_NAME = "web_push_vapid_private"


async def ensure_vapid_keys(db: Optional[AsyncSession] = None) -> None:
    """Bootstrap the VAPID keypair the DeeReach Web Push channel needs.

    Order of precedence:
      1. .env vars  (operator override; nothing to do)
      2. app_secrets DB row  (already provisioned on a prior boot)
      3. fresh keypair, written to app_secrets

    Race-safe across gunicorn workers + RQ worker(s): each process reads
    first, only one INSERT will commit, the others swallow IntegrityError
    and re-read.

    `db` lets the test suite inject a SQLite session; production callers
    pass None and we open a fresh AsyncSession on the global engine.
    """
    if settings.web_push_vapid_public_key and settings.web_push_vapid_private_key:
        return

    async def _do(session: AsyncSession) -> None:
        rows = (await session.exec(
            select(AppSecret).where(AppSecret.name.in_([PUB_KEY_NAME, PRIV_KEY_NAME]))
        )).all()
        existing = {row.name: row.value for row in rows}

        if PUB_KEY_NAME in existing and PRIV_KEY_NAME in existing:
            settings.web_push_vapid_public_key = existing[PUB_KEY_NAME]
            settings.web_push_vapid_private_key = existing[PRIV_KEY_NAME]
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
            log.info("Generated fresh VAPID keypair (web_push) — stored in app_secrets")
        except IntegrityError:
            await session.rollback()
            rows = (await session.exec(
                select(AppSecret).where(AppSecret.name.in_([PUB_KEY_NAME, PRIV_KEY_NAME]))
            )).all()
            existing = {row.name: row.value for row in rows}

        settings.web_push_vapid_public_key = existing.get(PUB_KEY_NAME, pub_b64)
        settings.web_push_vapid_private_key = existing.get(PRIV_KEY_NAME, priv_pem)

    if db is not None:
        await _do(db)
    else:
        async with AsyncSession(engine) as session:
            await _do(session)
