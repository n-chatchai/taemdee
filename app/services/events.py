"""Cross-worker event broker for live DeeBoard updates.

Runs over Postgres LISTEN/NOTIFY so events fan out across all gunicorn
workers. Each worker on startup:

  - opens one dedicated asyncpg Connection that LISTENs on `taemdee_events`
  - opens a small asyncpg Pool used by `publish()` to issue NOTIFY
  - keeps a local `_subscribers` dict for SSE clients connected to it

When any worker calls `publish()`, every worker (including itself) receives
the NOTIFY and dispatches to its own local subscribers. Without this, a
4-worker prod sees only ~1/4 of scans on the dashboard since the SSE
connection lives on a single random worker.

Tests run on SQLite — `start()` short-circuits and `publish()` falls back
to in-process dispatch, so no Postgres is required for the test suite.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import AsyncIterator, Dict, List, Optional
from uuid import UUID

import asyncpg

from app.core.config import settings

log = logging.getLogger(__name__)

CHANNEL = "taemdee_events"

# shop_id → list of subscriber queues (per-worker)
_subscribers: Dict[UUID, List[asyncio.Queue]] = defaultdict(list)
# customer_id → list of subscriber queues (per-worker). Same fan-out
# pattern as the shop side; payloads carry "customer_id" instead of
# "shop_id" so a single LISTEN connection routes both kinds.
_customer_subscribers: Dict[UUID, List[asyncio.Queue]] = defaultdict(list)
_listener_conn: Optional[asyncpg.Connection] = None
_publisher_pool: Optional[asyncpg.Pool] = None


def _pg_dsn() -> Optional[str]:
    """Return a plain asyncpg DSN, or None if the configured DB isn't Postgres."""
    url = settings.database_url
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://"):]
    if url.startswith("postgresql://"):
        return url
    return None


def _on_notify(connection, pid, channel, payload):
    """asyncpg listener callback — dispatch a NOTIFY payload to local queues."""
    try:
        msg = json.loads(payload)
        item = (msg["event_name"], msg["html"])
        if "shop_id" in msg:
            shop_id = UUID(msg["shop_id"])
            queues = _subscribers.get(shop_id, [])
        elif "customer_id" in msg:
            customer_id = UUID(msg["customer_id"])
            queues = _customer_subscribers.get(customer_id, [])
        else:
            return
        for q in queues:
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                # Drop for slow subscribers rather than block the listener.
                pass
    except Exception:
        log.exception("events: failed to dispatch notify payload=%r", payload)


async def start() -> None:
    """Open the LISTEN connection + publisher pool. Idempotent.

    Called from the FastAPI lifespan on each worker boot. If Postgres isn't
    configured (tests), this is a no-op and `publish()` uses local dispatch.
    """
    global _listener_conn, _publisher_pool
    if _listener_conn is not None:
        return
    dsn = _pg_dsn()
    if dsn is None:
        return
    try:
        _listener_conn = await asyncpg.connect(dsn)
        await _listener_conn.add_listener(CHANNEL, _on_notify)
        _publisher_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        log.info("events: LISTEN/NOTIFY started on channel=%s", CHANNEL)
    except Exception:
        log.exception("events: failed to start LISTEN/NOTIFY; falling back to local dispatch")
        if _listener_conn is not None:
            try:
                await _listener_conn.close()
            except Exception:
                pass
            _listener_conn = None
        _publisher_pool = None


async def stop() -> None:
    """Tear down the LISTEN connection + publisher pool on shutdown."""
    global _listener_conn, _publisher_pool
    if _listener_conn is not None:
        try:
            await _listener_conn.remove_listener(CHANNEL, _on_notify)
        except Exception:
            pass
        try:
            await _listener_conn.close()
        except Exception:
            pass
        _listener_conn = None
    if _publisher_pool is not None:
        try:
            await _publisher_pool.close()
        except Exception:
            pass
        _publisher_pool = None


async def _notify_async(shop_id: UUID, event_name: str, html: str) -> None:
    """Internal async path: send a NOTIFY via the publisher pool."""
    if _publisher_pool is None:
        return
    payload = json.dumps({
        "shop_id": str(shop_id),
        "event_name": event_name,
        "html": html,
    })
    try:
        async with _publisher_pool.acquire() as conn:
            await conn.execute("SELECT pg_notify($1, $2)", CHANNEL, payload)
    except Exception:
        log.exception("events: pg_notify failed for shop=%s event=%s", shop_id, event_name)


def publish(shop_id: UUID, event_name: str, html: str) -> None:
    """Broadcast an event to every dashboard listening for `shop_id`.

    Cross-worker via Postgres NOTIFY when initialized; in-process dispatch
    otherwise (tests, single-worker dev without Postgres). Fire-and-forget —
    the route doesn't await delivery so a slow NOTIFY can't stall the response.
    """
    if _publisher_pool is not None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_notify_async(shop_id, event_name, html))
            return
        except RuntimeError:
            # No running loop — should never happen from a route, but fall
            # through to local dispatch rather than crash.
            pass
    item = (event_name, html)
    for q in _subscribers.get(shop_id, []):
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass


def subscribe(shop_id: UUID) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _subscribers[shop_id].append(q)
    return q


def unsubscribe(shop_id: UUID, q: asyncio.Queue) -> None:
    if q in _subscribers.get(shop_id, []):
        _subscribers[shop_id].remove(q)


def feed_row_html(kind: str, item_id: UUID, when_label: str, customer_name: str = "ลูกค้า") -> str:
    """Render one feed row for the DeeBoard live feed (S3 dock) and SSE
    stream. Emits a `<tr class="feed-row">` carrying data-detail-url so a
    tap opens the S3.detail bottom sheet (where the void button lives now)."""
    label = '<span class="icon-mini">+</span><strong>1 แต้ม</strong>' if kind == "point" else "<strong>รับรางวัล</strong>"
    detail_url = f"/shop/feed/{kind}/{item_id}"
    return (
        f'<tr class="feed-row" id="row-{item_id}" data-detail-url="{detail_url}">'
        f'<td class="t">{when_label}</td>'
        f'<td class="n">{customer_name}</td>'
        f'<td class="a">{label}</td>'
        f"</tr>"
    )


# ---------------------------------------------------------------------------
# Customer-side parallel API. Same fan-out pattern, separate subscribers dict
# so a customer's stream doesn't see shop dashboard events and vice-versa.
# ---------------------------------------------------------------------------


async def _notify_customer_async(customer_id: UUID, event_name: str, html: str) -> None:
    if _publisher_pool is None:
        return
    payload = json.dumps({
        "customer_id": str(customer_id),
        "event_name": event_name,
        "html": html,
    })
    try:
        async with _publisher_pool.acquire() as conn:
            await conn.execute("SELECT pg_notify($1, $2)", CHANNEL, payload)
    except Exception:
        log.exception("events: pg_notify failed for customer=%s event=%s", customer_id, event_name)


def publish_customer(customer_id: UUID, event_name: str, html: str) -> None:
    """Fire-and-forget publish to a single customer's SSE subscribers.
    Mirrors `publish()` but routes to `_customer_subscribers`. Use from
    web routes; from a worker process where the loop terminates after
    the job, prefer `publish_customer_async` so the NOTIFY actually
    flushes before the loop closes."""
    if _publisher_pool is not None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_notify_customer_async(customer_id, event_name, html))
            return
        except RuntimeError:
            pass
    item = (event_name, html)
    for q in _customer_subscribers.get(customer_id, []):
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass


async def publish_customer_async(customer_id: UUID, event_name: str, html: str) -> None:
    """Awaitable customer publish — use from RQ worker context where the
    asyncio loop closes right after _run returns. Awaits the NOTIFY so
    the payload actually crosses the wire before the loop tears down."""
    if _publisher_pool is not None:
        await _notify_customer_async(customer_id, event_name, html)
        return
    item = (event_name, html)
    for q in _customer_subscribers.get(customer_id, []):
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass


def subscribe_customer(customer_id: UUID) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _customer_subscribers[customer_id].append(q)
    return q


def unsubscribe_customer(customer_id: UUID, q: asyncio.Queue) -> None:
    if q in _customer_subscribers.get(customer_id, []):
        _customer_subscribers[customer_id].remove(q)


async def stream_customer(customer_id: UUID) -> AsyncIterator[bytes]:
    """SSE wire format generator for a single customer's events. Caller
    wraps in StreamingResponse with media_type='text/event-stream'."""
    q = subscribe_customer(customer_id)
    try:
        yield b": connected\n\n"
        while True:
            try:
                event_name, html = await asyncio.wait_for(q.get(), timeout=20.0)
                data_lines = "\n".join(f"data: {line}" for line in html.splitlines() or [""])
                yield f"event: {event_name}\n{data_lines}\n\n".encode()
            except asyncio.TimeoutError:
                yield b": keep-alive\n\n"
    finally:
        unsubscribe_customer(customer_id, q)


async def stream(shop_id: UUID) -> AsyncIterator[bytes]:
    """SSE wire format generator. Caller wraps in StreamingResponse."""
    q = subscribe(shop_id)
    try:
        # Initial heartbeat so the browser confirms the stream is open.
        yield b": connected\n\n"
        while True:
            try:
                event_name, html = await asyncio.wait_for(q.get(), timeout=20.0)
                # Each `data:` line is one part; multi-line HTML must be flattened.
                data_lines = "\n".join(f"data: {line}" for line in html.splitlines() or [""])
                yield f"event: {event_name}\n{data_lines}\n\n".encode()
            except asyncio.TimeoutError:
                # Keep-alive comment every 20s so proxies don't drop idle streams.
                yield b": keep-alive\n\n"
    finally:
        unsubscribe(shop_id, q)
