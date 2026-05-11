"""External-platform webhooks. Currently: LINE Messaging API events.

The LINE Console points each Messaging channel at one HTTPS endpoint
that receives an `events[]` batch — follow / unfollow / message / etc.
We act on:
  · follow / unfollow → set User.line_friend_status
  · message.text      → mirror as an InboxReply on the customer's most
                        recent broadcast (last 48h) so a LINE reply
                        surfaces in /shop/messages just like an in-app
                        reply would

Everything else is logged and ignored. The body is HMAC-signed with
the channel secret; reject any request whose X-Line-Signature doesn't
verify.

Mounted at /webhooks/* from main.py — outside the subdomain-routing
allow-list those paths bypass on principle, since LINE doesn't know
about shop.* vs main domain. Add a registered route in the LINE
Console: https://taemdee.com/webhooks/line
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.models import Customer, Inbox, User
from app.models.util import utcnow
from app.services.inbox_reply import RateLimited, send_reply
from app.services.line_messaging import verify_signature

router = APIRouter()
log = logging.getLogger(__name__)

# How far back we'll attribute a LINE reply to a broadcast. Most
# replies arrive same-day; 48h covers next-morning catch-up replies
# without bleeding into "customer just chatting about something else"
# territory where the broadcast attribution is unsafe.
LINE_REPLY_ATTRIBUTION_HOURS = 48


@router.post("/line")
async def line_webhook(
    request: Request,
    x_line_signature: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Receive LINE webhook events. We care about:

    - `follow`     : customer added the OA → mark line_friend_status='friended'
    - `unfollow`   : customer removed the OA or blocked it → mark 'unfollowed'

    Everything else is logged at debug and acknowledged with 200 so
    LINE's delivery retries stop.
    """
    body = await request.body()
    if not verify_signature(body, x_line_signature):
        log.warning("line webhook signature verify failed")
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    # LINE sends a JSON envelope: {"destination": "...", "events": [...]}
    import json
    try:
        envelope = json.loads(body)
    except json.JSONDecodeError:
        log.warning("line webhook bad json")
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    events = envelope.get("events") or []
    for ev in events:
        ev_type = ev.get("type")
        source = ev.get("source") or {}
        line_id = source.get("userId")
        if not line_id:
            log.debug("line webhook event without userId: %s", ev_type)
            continue

        if ev_type == "follow":
            await _set_friend_status(db, line_id, "friended", clear_blocked=True)
            log.info("line follow → line_id=%s", line_id)
        elif ev_type == "unfollow":
            await _set_friend_status(db, line_id, "unfollowed", set_blocked=True)
            log.info("line unfollow → line_id=%s", line_id)
        elif ev_type == "message":
            # Only text messages get mirrored — stickers / images /
            # location etc. don't have a clean translation to an
            # InboxReply.body so they fall through to debug.
            message = ev.get("message") or {}
            if message.get("type") == "text":
                await _attribute_line_message_to_inbox(
                    db, line_id=line_id, text=(message.get("text") or ""),
                )
            else:
                log.debug("line webhook ignored message.type=%s", message.get("type"))
        else:
            log.debug("line webhook ignored event type=%s", ev_type)

    return Response(status_code=status.HTTP_200_OK)


async def _attribute_line_message_to_inbox(
    db: AsyncSession,
    *,
    line_id: str,
    text: str,
) -> None:
    """Mirror a customer's LINE reply into the broadcast-scoped inbox.

    The LINE OA is shared (one TaemDee OA across all shops) so the
    inbound text has no "which shop" context built in. We attach to
    the most recent broadcast the customer has received in the last
    LINE_REPLY_ATTRIBUTION_HOURS — same heuristic LINE chats follow
    ("reply is about the latest message you got"). When no broadcast
    is in window, the message is logged + ignored: it's likely a
    general-chat from a customer who didn't realise the OA isn't a
    support channel.

    Errors are swallowed (logged at WARNING). LINE retries failed
    webhook deliveries, so a flaky DB write doesn't lose the message —
    but we never want to 500 the webhook itself either.
    """
    body = (text or "").strip()
    if not body:
        return

    try:
        user = (await db.exec(
            select(User).where(User.line_id == line_id)
        )).first()
        if user is None:
            log.debug("line message: no user row for line_id=%s", line_id)
            return

        customer = (await db.exec(
            select(Customer).where(
                Customer.user_id == user.id,
                Customer.merged_into_id.is_(None),
            )
        )).first()
        if customer is None:
            log.debug("line message: no customer for line_id=%s", line_id)
            return

        cutoff = utcnow() - timedelta(hours=LINE_REPLY_ATTRIBUTION_HOURS)
        inbox = (await db.exec(
            select(Inbox).where(
                Inbox.customer_id == customer.id,
                Inbox.created_at >= cutoff,
            ).order_by(Inbox.created_at.desc()).limit(1)
        )).first()
        if inbox is None:
            log.debug(
                "line message: no broadcast in window for customer=%s, dropping",
                customer.id,
            )
            return

        try:
            await send_reply(db, inbox, sender="customer", body=body)
            log.info(
                "line message mirrored → customer=%s inbox=%s shop=%s",
                customer.id, inbox.id, inbox.shop_id,
            )
        except RateLimited:
            # send_reply enforces 3 customer replies per inbox per
            # minute. Hitting it via LINE means the customer just
            # spammed; drop silently — they already got their first
            # 3 mirrored.
            log.info(
                "line message rate-limited on inbox=%s for customer=%s",
                inbox.id, customer.id,
            )
    except Exception as e:  # noqa: BLE001
        log.warning("line message attribution failed line_id=%s: %s", line_id, e)
        try:
            await db.rollback()
        except Exception:
            pass


async def _set_friend_status(
    db: AsyncSession,
    line_id: str,
    status_value: str,
    *,
    clear_blocked: bool = False,
    set_blocked: bool = False,
) -> None:
    """Lookup the User by LINE userId and update their friend gate.

    Users who've never logged in via LINE (so line_id never captured)
    won't match — that's fine, the next time they log in they'll be
    flagged via the OAuth callback's identity claim and the next
    webhook hit will catch up.
    """
    result = await db.exec(select(User).where(User.line_id == line_id))
    user = result.first()
    if user is None:
        log.debug("line webhook: no user row for line_id=%s yet", line_id)
        return
    user.line_friend_status = status_value
    if clear_blocked:
        user.line_messaging_blocked_at = None
    if set_blocked:
        user.line_messaging_blocked_at = utcnow()
    db.add(user)
    await db.commit()
