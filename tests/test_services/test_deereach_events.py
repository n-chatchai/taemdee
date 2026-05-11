"""Unit tests for the engagement event log writer.

Covers the dedup policy (opened / voucher_claimed deduped per inbox,
replied not deduped), the payload pass-through, error swallowing,
and the by-id helper variant."""

from sqlmodel import select

from app.models import DeeReachEvent, Inbox
from app.services.deereach_events import (
    KIND_OPENED,
    KIND_REPLIED,
    KIND_VOUCHER_CLAIMED,
    log_event,
    log_event_by_inbox_id,
)


async def test_log_opened_creates_row_with_attribution(db, inbox_row):
    evt = await log_event(db, inbox=inbox_row, kind=KIND_OPENED)
    assert evt is not None
    assert evt.kind == KIND_OPENED
    assert evt.inbox_id == inbox_row.id
    assert evt.customer_id == inbox_row.customer_id
    assert evt.shop_id == inbox_row.shop_id
    assert evt.campaign_id == inbox_row.campaign_id
    assert evt.payload is None


async def test_log_opened_is_deduped_per_inbox(db, inbox_row):
    first = await log_event(db, inbox=inbox_row, kind=KIND_OPENED)
    second = await log_event(db, inbox=inbox_row, kind=KIND_OPENED)
    assert first is not None
    # Second call short-circuits — no new row, no error.
    assert second is None

    rows = (await db.exec(
        select(DeeReachEvent).where(
            DeeReachEvent.inbox_id == inbox_row.id,
            DeeReachEvent.kind == KIND_OPENED,
        )
    )).all()
    assert len(rows) == 1


async def test_log_replied_is_not_deduped(db, inbox_row):
    """Each customer reply is its own engagement event — re-logging
    with the same kind appends a fresh row."""
    a = await log_event(db, inbox=inbox_row, kind=KIND_REPLIED, payload={"reply_id": "a"})
    b = await log_event(db, inbox=inbox_row, kind=KIND_REPLIED, payload={"reply_id": "b"})
    assert a is not None and b is not None
    assert a.id != b.id

    rows = (await db.exec(
        select(DeeReachEvent).where(
            DeeReachEvent.inbox_id == inbox_row.id,
            DeeReachEvent.kind == KIND_REPLIED,
        )
    )).all()
    assert len(rows) == 2


async def test_log_voucher_claimed_is_deduped(db, inbox_row):
    a = await log_event(db, inbox=inbox_row, kind=KIND_VOUCHER_CLAIMED)
    b = await log_event(db, inbox=inbox_row, kind=KIND_VOUCHER_CLAIMED)
    assert a is not None
    assert b is None


async def test_log_event_serialises_payload_as_json(db, inbox_row):
    import json
    evt = await log_event(
        db, inbox=inbox_row, kind=KIND_REPLIED,
        payload={"reply_id": "abc-123", "extra": 42},
    )
    assert evt is not None
    assert evt.payload is not None
    decoded = json.loads(evt.payload)
    assert decoded == {"reply_id": "abc-123", "extra": 42}


async def test_log_event_by_inbox_id_hydrates_first(db, inbox_row):
    evt = await log_event_by_inbox_id(db, inbox_id=inbox_row.id, kind=KIND_OPENED)
    assert evt is not None
    assert evt.inbox_id == inbox_row.id


async def test_log_event_by_inbox_id_returns_none_for_unknown(db):
    from uuid import uuid4
    out = await log_event_by_inbox_id(db, inbox_id=uuid4(), kind=KIND_OPENED)
    assert out is None
