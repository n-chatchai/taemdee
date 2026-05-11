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

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request, Response, status
from loguru import logger as log
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.models import Customer, Inbox, User
from app.models.util import utcnow
from app.services.inbox_reply import RateLimited, send_reply
from app.services.line_messaging import mark_as_read, verify_signature

# loguru `logger` is configured at app startup with the project's
# global level/format — stdlib `logging.getLogger(__name__)` was
# defaulting to WARNING here and silently filtering the INFO drop
# paths a prod operator needs to see. Aliasing as `log` keeps every
# existing `log.info(...)` callsite intact.
router = APIRouter()

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
        header_state = "present" if x_line_signature else "missing"
        log.warning(
            f"line webhook signature verify failed "
            f"(header={header_state} body_bytes={len(body)})"
        )
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    # LINE sends a JSON envelope: {"destination": "...", "events": [...]}
    import json
    try:
        envelope = json.loads(body)
    except json.JSONDecodeError:
        log.warning("line webhook bad json")
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    events = envelope.get("events") or []
    destination = envelope.get("destination") or "?"
    log.info(f"line webhook received: events={len(events)} destination={destination}")
    for ev in events:
        ev_type = ev.get("type")
        source = ev.get("source") or {}
        line_id = source.get("userId")
        if not line_id:
            log.info(f"line webhook event without userId: type={ev_type}")
            continue

        if ev_type == "follow":
            await _set_friend_status(db, line_id, "friended", clear_blocked=True)
            log.info(f"line follow → line_id={line_id}")
        elif ev_type == "unfollow":
            await _set_friend_status(db, line_id, "unfollowed", set_blocked=True)
            log.info(f"line unfollow → line_id={line_id}")
        elif ev_type == "message":
            # Only text messages get mirrored — stickers / images /
            # location etc. don't have a clean translation to an
            # InboxReply.body so they fall through.
            message = ev.get("message") or {}
            msg_type = message.get("type")
            if msg_type == "text":
                text_len = len(message.get("text") or "")
                log.info(
                    f"line message.text received: line_id={line_id} len={text_len}"
                )
                await _attribute_line_message_to_inbox(
                    db, line_id=line_id, text=(message.get("text") or ""),
                )
            else:
                log.info(f"line webhook ignored message.type={msg_type}")
        else:
            log.info(f"line webhook ignored event type={ev_type}")

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
            log.info(
                f"line message attribution: NO USER for line_id={line_id} "
                f"(customer never linked LINE via OAuth or line_id mismatch)"
            )
            return

        customer = (await db.exec(
            select(Customer).where(
                Customer.user_id == user.id,
                Customer.merged_into_id.is_(None),
            )
        )).first()
        if customer is None:
            log.info(
                f"line message attribution: NO CUSTOMER for user={user.id} "
                f"line_id={line_id} (user row exists but no Customer attached, "
                f"or all merged)"
            )
            return

        cutoff = utcnow() - timedelta(hours=LINE_REPLY_ATTRIBUTION_HOURS)
        inbox = (await db.exec(
            select(Inbox).where(
                Inbox.customer_id == customer.id,
                Inbox.created_at >= cutoff,
            ).order_by(Inbox.created_at.desc()).limit(1)
        )).first()
        if inbox is None:
            log.info(
                f"line message attribution: NO BROADCAST in last "
                f"{LINE_REPLY_ATTRIBUTION_HOURS}h for customer={customer.id} "
                f"— message dropped, customer is likely chatting general "
                f"(no broadcast context to attach the reply to)"
            )
            return

        try:
            reply = await send_reply(
                db, inbox, sender="customer", body=body, source="line",
            )
            read_state = "already read" if inbox.read_at else "unread before"
            log.info(
                f"line message MIRRORED → customer={customer.id} "
                f"inbox={inbox.id} shop={inbox.shop_id} reply={reply.id} "
                f"(broadcast was {read_state}; now marked read)"
            )
            # Drop the LINE OA Manager unread badge — operator has
            # already seen the message on /shop/messages, no need
            # for LINE's own inbox UI to keep flagging it. Best-
            # effort; failure here doesn't roll back the mirror.
            await mark_as_read(line_id)
        except RateLimited:
            # send_reply enforces 3 customer replies per inbox per
            # minute. Hitting it via LINE means the customer just
            # spammed; drop silently — they already got their first
            # 3 mirrored.
            log.info(
                f"line message RATE-LIMITED on inbox={inbox.id} "
                f"for customer={customer.id} (>3 replies/min on same broadcast)"
            )
    except Exception as e:  # noqa: BLE001
        # loguru doesn't accept the stdlib `exc_info` kwarg; use
        # .exception() instead so the traceback still shows up.
        log.exception(f"line message attribution FAILED line_id={line_id}: {e}")
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
        log.info(
            f"line webhook friend status: no User row for line_id={line_id} "
            f"yet (will be backfilled on next OAuth login)"
        )
        return
    user.line_friend_status = status_value
    if clear_blocked:
        user.line_messaging_blocked_at = None
    if set_blocked:
        user.line_messaging_blocked_at = utcnow()
    db.add(user)
    await db.commit()
