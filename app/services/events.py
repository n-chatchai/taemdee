"""In-process event broker for live DeeBoard updates.

When something happens at a shop (stamp issued, redemption, void), services
publish an event keyed by shop_id. The SSE endpoint at `/shop/events`
subscribes per request and streams events to the connected dashboard.

This is single-process — fine for v1 / single uvicorn worker. Multi-worker
deployments should swap to Redis pub/sub before scaling.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import AsyncIterator, Dict, List
from uuid import UUID

# shop_id → list of subscriber queues
_subscribers: Dict[UUID, List[asyncio.Queue]] = defaultdict(list)


def publish(shop_id: UUID, event_name: str, html: str) -> None:
    """Push a pre-rendered HTML fragment to every subscriber of this shop.

    `event_name` becomes the SSE `event:` field (e.g., "feed-row", "void").
    `html` is what the client will receive in `data:`.
    """
    payload = (event_name, html)
    for q in _subscribers.get(shop_id, []):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            # Drop the event for this slow subscriber rather than blocking the publisher.
            pass


def subscribe(shop_id: UUID) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _subscribers[shop_id].append(q)
    return q


def unsubscribe(shop_id: UUID, q: asyncio.Queue) -> None:
    if q in _subscribers.get(shop_id, []):
        _subscribers[shop_id].remove(q)


def feed_row_html(kind: str, item_id: UUID, when_iso: str) -> str:
    """Render one feed row used by the DeeBoard live feed (S3) and SSE stream."""
    short = item_id.hex[:4].upper()
    label = '<span class="icon-mini">+</span><strong>1 แต้ม</strong>' if kind == "stamp" else "<strong>รับรางวัล</strong>"
    void_url = f"/shop/{'stamps' if kind == 'stamp' else 'redemptions'}/{item_id}/void"
    return (
        f'<div class="feed-row" id="row-{item_id}">'
        f'<div class="t">{when_iso}</div>'
        f'<div class="w">{label} <span class="id">#{short}</span></div>'
        f'<button class="void" data-void-url="{void_url}" data-row="row-{item_id}">ยกเลิก</button>'
        f"</div>"
    )


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
