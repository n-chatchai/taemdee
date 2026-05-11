"""Reply lifecycle for the broadcast-scoped inbox model.

Replaces the old customer_chat module. Replies are always parented on
an Inbox row (a DeeReach broadcast that landed in the customer's
inbox), so the data model rules out customer-initiated chat by design.

Per-side responsibilities:
  · send_reply(inbox_id, sender='customer'|'shop', body): create the
    InboxReply row, fire the SSE event for the OTHER side's open
    detail page (so the new comment appears live), and roll the
    unread badges.
  · mark_shop_read(inbox_id): set shop_read_at on every customer-sender
    reply for that inbox — fired when the operator opens the detail
    page so the unread badge in /shop/messages drops.
  · mark_customer_read(inbox_id): clear Inbox.read_at NULL → utcnow()
    when the customer opens the detail page (replaces the read flag we
    used to flip in routes/customer.py).
"""

import html as _html
import json
from datetime import timedelta
from typing import List
from uuid import UUID

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Inbox, InboxReply
from app.models.util import utcnow


# Customer can send at most this many replies to a single broadcast in
# the trailing window — guards against automated abuse. Shop side is
# unrestricted; replying to your own customers is the desired behaviour.
CUSTOMER_RATE_WINDOW = timedelta(minutes=1)
CUSTOMER_RATE_LIMIT = 3


class RateLimited(Exception):
    """Raised by send_reply when the customer has hit
    CUSTOMER_RATE_LIMIT replies within CUSTOMER_RATE_WINDOW on this
    inbox row."""


async def list_replies(
    db: AsyncSession, inbox_id: UUID, *, limit: int = 200
) -> List[InboxReply]:
    """Oldest-first reply list for the broadcast detail page."""
    rows = (await db.exec(
        select(InboxReply)
        .where(InboxReply.inbox_id == inbox_id)
        .order_by(InboxReply.created_at)
        .limit(limit)
    )).all()
    return list(rows)


async def _customer_recent_count(
    db: AsyncSession, inbox_id: UUID
) -> int:
    cutoff = utcnow() - CUSTOMER_RATE_WINDOW
    rows = (await db.exec(
        select(InboxReply).where(
            InboxReply.inbox_id == inbox_id,
            InboxReply.sender == "customer",
            InboxReply.created_at >= cutoff,
        )
    )).all()
    return len(list(rows))


async def send_reply(
    db: AsyncSession,
    inbox: Inbox,
    *,
    sender: str,
    body: str,
    source: str = "app",
) -> InboxReply:
    """Append a reply onto the broadcast and notify the other side via
    SSE. Returns the persisted InboxReply.

    sender:
      · 'customer' → reply enters the conversation, customer-rate-
        limited; shop's unread badge increments.
      · 'shop'     → operator reply; resets Inbox.read_at to NULL so
        the customer sees the broadcast as unread again on next list
        render.

    source: where the reply originated. Default 'app' covers the
    in-app POST endpoints; 'line' marks replies mirrored from the
    @taemdee OA chat webhook. The shop-side thread renders a small
    "ผ่าน LINE" pill on non-'app' rows so the operator knows the
    customer used the external channel.
    """
    text = (body or "").strip()
    if not text:
        raise ValueError("body required")
    if sender not in ("customer", "shop"):
        raise ValueError(f"invalid sender: {sender}")

    if sender == "customer":
        recent = await _customer_recent_count(db, inbox.id)
        if recent >= CUSTOMER_RATE_LIMIT:
            raise RateLimited()

    reply = InboxReply(inbox_id=inbox.id, sender=sender, body=text, source=source)
    db.add(reply)

    # When the shop replies, kick the broadcast back to "unread" on the
    # customer side — the shop's reply IS new content for the customer
    # to read, even if the original broadcast was already opened.
    #
    # When the customer replies, the broadcast is implicitly read —
    # the customer obviously saw it before composing a reply. For
    # in-app replies the GET handler already flipped Inbox.read_at,
    # so this branch is a no-op there; the path that matters is
    # LINE-attributed replies (no preceding GET) where the broadcast
    # would otherwise stay marked unread on the customer's dock.
    customer_read_flipped = False
    if sender == "shop":
        inbox.read_at = None
        db.add(inbox)
    elif sender == "customer" and inbox.read_at is None:
        inbox.read_at = utcnow()
        db.add(inbox)
        customer_read_flipped = True

    await db.commit()
    await db.refresh(reply)

    # Engagement log — only customer-side replies count as engagement
    # signal (shop replies are the operator working their queue, not
    # a signal from the audience). Log AFTER commit so the reply id
    # is stable enough to reference in the payload.
    if sender == "customer":
        from app.services.deereach_events import KIND_REPLIED, log_event
        await log_event(
            db, inbox=inbox, kind=KIND_REPLIED,
            payload={"reply_id": str(reply.id)},
        )

    await _publish_reply_events(
        db, inbox, reply,
        customer_inbox_count_changed=customer_read_flipped,
    )
    return reply


async def mark_shop_read(db: AsyncSession, inbox: Inbox) -> int:
    """Stamp shop_read_at on every customer-sender reply for this
    inbox. Returns how many rows were updated. Called when the shop
    opens /shop/messages/<inbox_id> so the unread chip on the list
    drops to zero."""
    rows = (await db.exec(
        select(InboxReply).where(
            InboxReply.inbox_id == inbox.id,
            InboxReply.sender == "customer",
            InboxReply.shop_read_at.is_(None),
        )
    )).all()
    n = 0
    now = utcnow()
    for r in rows:
        r.shop_read_at = now
        db.add(r)
        n += 1
    if n:
        await db.commit()
    return n


async def shop_unread_inbox_ids(db: AsyncSession, shop_id: UUID) -> List[UUID]:
    """Inbox rows under this shop that carry at least one unread
    customer reply. Used to badge /shop/messages list rows + the dock
    counter."""
    rows = (await db.exec(
        select(Inbox.id)
        .join(InboxReply, InboxReply.inbox_id == Inbox.id)
        .where(
            Inbox.shop_id == shop_id,
            InboxReply.sender == "customer",
            InboxReply.shop_read_at.is_(None),
        )
        .group_by(Inbox.id)
    )).all()
    return [r for r in rows]


async def shop_unread_total(db: AsyncSession, shop_id: UUID) -> int:
    """Count of customer-sender replies under this shop that haven't
    been opened by the operator yet — drives the dock badge."""
    n = (await db.exec(
        select(func.count())
        .select_from(InboxReply)
        .join(Inbox, Inbox.id == InboxReply.inbox_id)
        .where(
            Inbox.shop_id == shop_id,
            InboxReply.sender == "customer",
            InboxReply.shop_read_at.is_(None),
        )
    )).one()
    return int(n or 0)


async def customer_unread_total(
    db: AsyncSession, customer_id: UUID
) -> int:
    """Inbox rows for this customer with read_at IS NULL — the count
    drives the customer-side dock badge. When the shop replies on a
    broadcast we reset Inbox.read_at to None (see send_reply), so a
    shop reply bumps this counter the same way a fresh broadcast does."""
    n = (await db.exec(
        select(func.count()).select_from(Inbox).where(
            Inbox.customer_id == customer_id,
            Inbox.read_at.is_(None),
        )
    )).one()
    return int(n or 0)


def _comment_html(
    reply: InboxReply,
    *,
    customer_label: str,
    customer_initial: str,
    shop_label: str,
    shop_initial: str,
) -> str:
    """Render one ix-comment row that the SSE listener appends to the
    open detail page. Mirrors design/taemdee-shop.html → inbox.message:
    avatar chip + author + time + body. Shop-sender rows get the
    `.shop` modifier so the avatar flips to ink. Same markup serves
    customer + shop sides (the listener doesn't need a viewer flag —
    rendered comments are author-anchored, not viewer-anchored).
    """
    is_shop = reply.sender == "shop"
    cls = "ix-comment shop" if is_shop else "ix-comment"
    initial = _html.escape(shop_initial if is_shop else customer_initial)
    author = _html.escape(shop_label if is_shop else customer_label)
    body_html = _html.escape(reply.body or "")
    time_str = reply.created_at.strftime("%H:%M")
    # Source pill — only non-'app' origins get marked, so in-app
    # replies stay visually quiet and out-of-band channels (LINE
    # today, SMS / web_push later) surface with a small chip.
    source = (reply.source or "app").lower()
    via_html = ""
    if source == "line":
        via_html = '<span class="ixc-via line">ผ่าน LINE</span>'
    elif source != "app":
        # Forward-compat: any unknown source still gets a chip with
        # the raw label so it's visible during integration testing.
        via_html = f'<span class="ixc-via">ผ่าน {_html.escape(source)}</span>'
    return (
        f'<div class="{cls}" data-reply-id="{reply.id}">'
        f'<div class="ixc-avatar">{initial}</div>'
        f'<div class="ixc-body">'
        f'<div class="ixc-head">'
        f'<span class="ixc-author">{author}</span>'
        f'{via_html}'
        f'<span class="ixc-time">{time_str}</span>'
        f'</div>'
        f'<div class="ixc-text">{body_html}</div>'
        f'</div>'
        f'</div>'
    )


async def _publish_reply_events(
    db: AsyncSession,
    inbox: Inbox,
    reply: InboxReply,
    *,
    customer_inbox_count_changed: bool = False,
) -> None:
    """Fire SSE events to the side that DIDN'T just send so their open
    detail page picks the comment up live. No web push for replies —
    only the broadcast itself ever sends a push.

    `customer_inbox_count_changed=True` (set when send_reply flipped
    Inbox.read_at as a side-effect of the customer's reply — typically
    a LINE-attributed reply) also fires an `inbox-update` to the
    customer side so their own dock badge decrements live, matching
    what the my_inbox_detail GET handler does on in-app open."""
    from loguru import logger
    from app.models import Customer, Shop
    from app.services.events import publish, publish_customer

    # Hydrate the labels + initials once so a single rendered comment
    # works on both sides — the markup is author-anchored, not
    # viewer-anchored, so we don't need separate variants.
    customer = await db.get(Customer, inbox.customer_id)
    shop = await db.get(Shop, inbox.shop_id)
    customer_label = (customer.display_name if customer else None) or "พี่"
    shop_label = (shop.name if shop else None) or "ร้าน"
    comment = _comment_html(
        reply,
        customer_label=customer_label,
        customer_initial=(customer_label[:1] or "ก").upper(),
        shop_label=shop_label,
        shop_initial=(shop_label[:1] or "ร").upper(),
    )

    if reply.sender == "customer":
        publish(inbox.shop_id, "inbox-reply-in", json.dumps({
            "inbox_id": str(inbox.id),
            "html": comment,
        }))
        new_total = await shop_unread_total(db, inbox.shop_id)
        publish(inbox.shop_id, "messages-update", str(new_total))
        logger.info(
            f"📬 inbox reply (customer→shop): shop={inbox.shop_id} "
            f"inbox={inbox.id} reply={reply.id} new_unread={new_total}"
        )
        # Customer-side dock badge update — only fires when this reply
        # also moved Inbox.read_at from NULL to now (LINE-attributed
        # reply path). In-app replies don't need it because the GET
        # handler that opened the detail page already published this
        # event before the reply was composed.
        if customer_inbox_count_changed:
            customer_unread = await customer_unread_total(db, inbox.customer_id)
            publish_customer(
                inbox.customer_id, "inbox-update", str(customer_unread),
            )
    else:  # shop reply
        publish_customer(inbox.customer_id, "inbox-reply-in", json.dumps({
            "inbox_id": str(inbox.id),
            "html": comment,
        }))
        merged = await customer_unread_total(db, inbox.customer_id)
        publish_customer(inbox.customer_id, "inbox-update", str(merged))
        logger.info(
            f"📬 inbox reply (shop→customer): customer={inbox.customer_id} "
            f"inbox={inbox.id} reply={reply.id} new_unread={merged}"
        )
