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


def stamp_toast_html(stamp_id: UUID, current_count: int, threshold: int) -> str:
    """Render the S6 stamp-notification toast pushed to the DeeBoard via SSE.

    Shows up briefly when a customer scans, with the customer's running progress
    + a [Void] button (60-sec window). Self-dismisses via dashboard JS.
    """
    short = stamp_id.hex[:4].upper()
    void_url = f"/shop/stamps/{stamp_id}/void"
    threshold = max(threshold, 1)
    just_now_idx = max(min(current_count, threshold), 1)
    cells = []
    for i in range(1, threshold + 1):
        if i == just_now_idx and current_count <= threshold:
            cells.append('<div class="ms just-now"></div>')
        elif i <= current_count:
            cells.append('<div class="ms on"></div>')
        else:
            cells.append('<div class="ms"></div>')
    stamps_grid = "".join(cells)
    return (
        f'<div class="s6-overlay s6-modal" id="toast-{stamp_id}" data-stamp-id="{stamp_id}">'
        f'<div class="s6-toast">'
        f'<div class="top-row">'
        f'<div class="plus">+1</div>'
        f'<div class="info">'
        f'<div class="h">ออกแต้มสำเร็จ</div>'
        f'<div class="s">ลูกค้า · #{short}</div>'
        f"</div></div>"
        f'<div class="progress-mini">'
        f'<div class="row">'
        f'<div class="name">ลูกค้าคนนี้สะสมไว้</div>'
        f'<div class="count">{current_count}<span class="of">/{threshold}</span></div>'
        f"</div>"
        f'<div class="stamps-mini">{stamps_grid}</div>'
        f"</div>"
        f'<div class="void-row">'
        f'<button class="void-btn" data-void-url="{void_url}" data-toast="toast-{stamp_id}">ยกเลิกแต้มนี้</button>'
        f'<div class="countdown" data-deadline="60">60 วิ</div>'
        f"</div>"
        f"</div></div>"
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
