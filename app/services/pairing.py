"""Pairing service — issue codes, claim them from OAuth callbacks, redeem
them from PWA. See docs/pwa-oauth-pairing.md for the full design."""

from __future__ import annotations

import asyncio
import secrets
from datetime import timedelta
from typing import Optional
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Pairing
from app.models.util import utcnow

PAIRING_TTL_MINUTES = 10
PWA_TOKEN_COOKIE = "td_pair_pwa"


async def create_pairing(
    db: AsyncSession,
    *,
    originator_customer_id: Optional[UUID] = None,
) -> Pairing:
    """Mint a fresh Pairing row. The route is responsible for setting the
    `pwa_token` cookie on the response (cookies need a Response, which the
    service shouldn't know about). `originator_customer_id` carries the
    PWA caller's identity through to the OAuth callback so the new
    provider binds to the same customer/user instead of a cookie-less
    fresh row."""
    code = secrets.token_urlsafe(32)
    pwa_token = secrets.token_urlsafe(32)
    row = Pairing(
        code=code,
        pwa_token=pwa_token,
        originator_customer_id=originator_customer_id,
        expires_at=utcnow() + timedelta(minutes=PAIRING_TTL_MINUTES),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def find_active_pairing(db: AsyncSession, code: str) -> Optional[Pairing]:
    """Lookup helper used by /events, /redeem, and the OAuth callbacks.
    Returns None for unknown / expired codes so callers don't have to
    repeat the same TTL check."""
    row = (await db.exec(
        select(Pairing).where(Pairing.code == code).limit(1)
    )).first()
    if row is None:
        return None
    if row.expires_at <= utcnow():
        return None
    return row


async def claim_pairing(
    db: AsyncSession,
    code: str,
    customer_id: UUID,
    provider: str,
) -> Optional[Pairing]:
    """Called from OAuth callbacks when state carries `?pair=<code>`.
    Idempotent: a second claim with the same customer is a no-op; a
    different customer trying to hijack returns None.
    """
    from loguru import logger

    row = await find_active_pairing(db, code)
    if row is None:
        logger.warning(
            "claim_pairing code={} rejected: row missing or expired",
            code[:8],
        )
        return None
    if row.customer_id is None:
        row.customer_id = customer_id
        row.provider = provider
        db.add(row)
        await db.commit()
        await db.refresh(row)
        logger.info(
            "claim_pairing code={} OK provider={} customer={}",
            code[:8], provider, customer_id,
        )
    elif row.customer_id != customer_id:
        logger.warning(
            "claim_pairing code={} rejected: hijack attempt (existing={} vs new={})",
            code[:8], row.customer_id, customer_id,
        )
        return None  # claim conflict; refuse
    else:
        logger.info(
            "claim_pairing code={} no-op (already claimed by same customer)",
            code[:8],
        )
    # Notify any SSE listeners. Use the events module's NOTIFY pool when
    # available so multi-worker setups fan out; otherwise the local
    # asyncio.Event below covers the same-process polling fallback.
    _local_signal(code)
    return row


async def redeem_pairing(
    db: AsyncSession,
    code: str,
    pwa_token: Optional[str],
) -> Optional[Pairing]:
    """Verify the pwa_token matches the row, the row is claimed, and
    return the row so the caller can set the customer cookie.

    Idempotent: if the row was already redeemed AND the pwa_token still
    matches, return success again. A network blip on the first redeem
    response would otherwise leave the PWA polling forever even though
    the server-side state was committed.
    """
    from loguru import logger

    if not pwa_token:
        logger.warning("redeem code={} rejected: pwa_token missing", code[:8])
        return None
    row = await find_active_pairing(db, code)
    if row is None:
        logger.warning("redeem code={} rejected: row missing or expired", code[:8])
        return None
    if row.pwa_token != pwa_token:
        logger.warning(
            "redeem code={} rejected: pwa_token mismatch (sent={} vs row={})",
            code[:8], pwa_token[:6], row.pwa_token[:6],
        )
        return None
    if row.customer_id is None:
        logger.warning(
            "redeem code={} rejected: not claimed yet (callback hasn't run?)",
            code[:8],
        )
        return None
    if row.redeemed_at is not None:
        # Idempotent re-redeem: the first call's response probably got
        # dropped before reaching the PWA. Return the row again so the
        # PWA gets a fresh customer cookie on this attempt and reloads.
        logger.info(
            "redeem code={} idempotent re-redeem (first call was at {})",
            code[:8], row.redeemed_at,
        )
        return row
    row.redeemed_at = utcnow()
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info("redeem code={} OK customer={}", code[:8], row.customer_id)
    return row


# ---------------------------------------------------------------------------
# Local in-process signaling for the SSE endpoint. Each waiting code keeps
# an asyncio.Event; claim_pairing flips it. Single-worker dev uses just
# this; for multi-worker production the events module's pg_notify fan-out
# would be the correct path — but a pairing flow is one device-pair-PWA
# interaction so falling through to a 2-second poll on the DB is also
# acceptable. SSE handler does both: subscribes to the local signal AND
# polls the DB on a slow timer.
# ---------------------------------------------------------------------------

_local_events: dict[str, asyncio.Event] = {}


def _local_signal(code: str) -> None:
    ev = _local_events.get(code)
    if ev is not None:
        ev.set()


def get_or_create_local_event(code: str) -> asyncio.Event:
    ev = _local_events.get(code)
    if ev is None:
        ev = asyncio.Event()
        _local_events[code] = ev
    return ev


def drop_local_event(code: str) -> None:
    _local_events.pop(code, None)
