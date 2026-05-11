"""DeeReach engagement event logging.

Single entry point — `log_event` — that fires from the routes / services
that detect an engagement signal (inbox opened, reply sent, voucher
claimed). Stats page + customer engagement score read off this table
later; for now this module is just the writer.

Idempotency policy:
  · `opened` is deduplicated per (inbox, kind) so a reload / re-render
    after the first paint doesn't double-count.
  · `replied` is never deduplicated — each reply is a distinct
    engagement signal even within the same inbox.
  · `voucher_claimed` is reserved for future use; same per-inbox
    dedup as `opened` so re-render doesn't inflate.

Errors are swallowed (logged at WARNING). Engagement is a derived
signal — never block the user-facing action if the log fails.
"""

import json
from typing import Optional
from uuid import UUID

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import DeeReachEvent, Inbox


# Kinds — kept as module constants so callers can `KIND_OPENED`
# instead of magic-stringing "opened". Adding a new kind is a
# one-line append; the DB column is a free-form VARCHAR.
KIND_OPENED = "opened"
KIND_REPLIED = "replied"
KIND_VOUCHER_CLAIMED = "voucher_claimed"

_DEDUPED_KINDS = {KIND_OPENED, KIND_VOUCHER_CLAIMED}


async def log_event(
    db: AsyncSession,
    *,
    inbox: Inbox,
    kind: str,
    payload: Optional[dict] = None,
) -> Optional[DeeReachEvent]:
    """Persist an engagement event tied to a broadcast inbox row.

    Resolves customer_id / shop_id / campaign_id from the Inbox so
    the caller only needs to pass the row + kind. Returns the
    created DeeReachEvent, or None if a dedup hit means we didn't
    create one.

    Never raises — logs WARNING + returns None on any DB error so a
    failed engagement log doesn't bubble up to the user.
    """
    try:
        if kind in _DEDUPED_KINDS:
            existing = (await db.exec(
                select(DeeReachEvent).where(
                    DeeReachEvent.inbox_id == inbox.id,
                    DeeReachEvent.kind == kind,
                ).limit(1)
            )).first()
            if existing is not None:
                return None

        evt = DeeReachEvent(
            inbox_id=inbox.id,
            customer_id=inbox.customer_id,
            shop_id=inbox.shop_id,
            campaign_id=inbox.campaign_id,
            kind=kind,
            payload=json.dumps(payload) if payload else None,
        )
        db.add(evt)
        await db.commit()
        await db.refresh(evt)
        return evt
    except Exception as e:  # noqa: BLE001
        logger.warning(f"log_event({kind}) failed for inbox={inbox.id}: {e}")
        try:
            await db.rollback()
        except Exception:
            pass
        return None


async def log_event_by_inbox_id(
    db: AsyncSession,
    *,
    inbox_id: UUID,
    kind: str,
    payload: Optional[dict] = None,
) -> Optional[DeeReachEvent]:
    """Variant that hydrates the inbox row first — used by callers
    that only have the id (e.g. async post-request hooks where the
    original Inbox instance has gone out of scope)."""
    inbox = await db.get(Inbox, inbox_id)
    if inbox is None:
        return None
    return await log_event(db, inbox=inbox, kind=kind, payload=payload)
