"""Customer ↔ Shop chat lifecycle.

One CustomerThread per (customer, shop) pair (UNIQUE in the DB).
`send_message` enforces a per-customer-per-shop rate limit; the
shop side has no rate limit (replying is the desired behaviour).
"""

import html as _html
import json
from datetime import timedelta
from typing import List, Optional, Tuple
from uuid import UUID

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import CustomerMessage, CustomerThread, Inbox
from app.models.util import utcnow


# Customer can send at most this many messages to a single shop in
# the trailing window — guards the inbox against accidental spam +
# automated abuse. The shop side is unrestricted (a flood of replies
# is, by design, not the same kind of risk).
CUSTOMER_RATE_WINDOW = timedelta(minutes=1)
CUSTOMER_RATE_LIMIT = 3


class RateLimited(Exception):
    """Raised by send_message when the customer has hit
    CUSTOMER_RATE_LIMIT messages within CUSTOMER_RATE_WINDOW for
    this thread."""


async def get_or_create_thread(
    db: AsyncSession, *, customer_id: UUID, shop_id: UUID
) -> CustomerThread:
    """Find the existing thread for this (customer, shop) pair, or
    create one. Race-safe enough for the volume we expect here —
    duplicate inserts would 500 on the unique index, the next call
    finds the winner."""
    row = (await db.exec(
        select(CustomerThread).where(
            CustomerThread.customer_id == customer_id,
            CustomerThread.shop_id == shop_id,
        )
    )).first()
    if row is not None:
        return row
    row = CustomerThread(customer_id=customer_id, shop_id=shop_id)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _customer_recent_count(
    db: AsyncSession, thread: CustomerThread
) -> int:
    cutoff = utcnow() - CUSTOMER_RATE_WINDOW
    rows = (await db.exec(
        select(CustomerMessage).where(
            CustomerMessage.thread_id == thread.id,
            CustomerMessage.sender == "customer",
            CustomerMessage.created_at >= cutoff,
        )
    )).all()
    return len(list(rows))


async def send_message(
    db: AsyncSession,
    thread: CustomerThread,
    *,
    sender: str,  # 'customer' | 'shop'
    body: str,
    attachment_url: Optional[str] = None,
) -> CustomerMessage:
    """Append a message to the thread, bump unread for the OTHER side,
    push last_at, and (for customer messages) enforce the rate limit.
    Returns the new CustomerMessage."""
    if sender not in ("customer", "shop"):
        raise ValueError(f"sender must be 'customer' or 'shop', got {sender!r}")
    body = (body or "").strip()
    if not body and not attachment_url:
        raise ValueError("message must have body or attachment")

    if sender == "customer":
        if await _customer_recent_count(db, thread) >= CUSTOMER_RATE_LIMIT:
            raise RateLimited(
                f"ส่งได้ไม่เกิน {CUSTOMER_RATE_LIMIT} ข้อความต่อนาที · "
                "รอสักครู่นะครับ"
            )

    msg = CustomerMessage(
        thread_id=thread.id,
        sender=sender,
        body=body,
        attachment_url=attachment_url,
    )
    db.add(msg)

    now = utcnow()
    thread.last_at = now
    if sender == "customer":
        thread.shop_unread += 1
    else:
        thread.customer_unread += 1
    db.add(thread)

    await db.commit()
    await db.refresh(msg)
    await db.refresh(thread)

    # Live SSE — push the new bubble + bumped badge to the OTHER side
    # so an open thread page appends in real time and the dock badge
    # ticks without a full reload.
    await _publish_chat_events(db, thread, msg, sender)

    return msg


def _bubble_html(
    sender: str,
    msg: CustomerMessage,
    *,
    viewer: str = "customer",
) -> str:
    """Render one chat fragment that the SSE listener appends to the
    open thread page. Mirrors design/taemdee-customer.html →
    inbox.message — `<div class="chat-bubble {side}" data-msg-id>` +
    a sibling `<div class="chat-time {side}">`. Server-rendered
    messages in the template emit the same pair, so a live-appended
    bubble looks identical to the initial paint.

    `viewer` decides which sender flips to `me` (right, ink fill) vs
    `them` (left, surface fill). Customer-side thread page passes
    "customer", so the viewer's own bubbles render as me. Future
    shop-side reuse can pass "shop".
    """
    side = "me" if sender == viewer else "them"
    body_html = _html.escape(msg.body or "")
    time_str = msg.created_at.strftime("%H:%M")
    return (
        f'<div class="chat-bubble {side}" data-msg-id="{msg.id}">{body_html}</div>'
        f'<div class="chat-time {side}">{time_str}</div>'
    )


async def _publish_chat_events(
    db: AsyncSession,
    thread: CustomerThread,
    msg: CustomerMessage,
    sender: str,
) -> None:
    """Fire the SSE events for the side that DIDN'T just send, plus
    a Web Push notification to the same side so the recipient gets a
    system-level alert when the PWA isn't on the foreground tab."""
    from loguru import logger
    from app.services.events import publish, publish_customer
    from app.services.web_push import send_to_user

    body_preview = (msg.body or "[แนบไฟล์]").strip()
    if len(body_preview) > 140:
        body_preview = body_preview[:137] + "…"

    # Render two bubble fragments — one targeted at the customer-side
    # thread page (viewer="customer", so customer messages flip to me)
    # and one at the shop-side. The SSE listeners on each side receive
    # only their own variant, keeping side alignment consistent.
    bubble_for_customer = _bubble_html(sender, msg, viewer="customer")
    bubble_for_shop = _bubble_html(sender, msg, viewer="shop")

    if sender == "customer":
        # Notify the shop's open thread page + bump the dock badge.
        publish(thread.shop_id, "chat-message-in", json.dumps({
            "thread_id": str(thread.id),
            "html": bubble_for_shop,
        }))
        new_total = await shop_unread_total(db, thread.shop_id)
        publish(thread.shop_id, "messages-update", str(new_total))
        logger.info(
            f"💬 chat publish (customer→shop): shop={thread.shop_id} "
            f"thread={thread.id} msg={msg.id} new_unread={new_total}"
        )
        # Web Push to every staff member of this shop with a saved
        # subscription. Title is the customer's display name so the
        # operator immediately sees who replied.
        from app.models import Customer, StaffMember, User
        customer = await db.get(Customer, thread.customer_id)
        cust_user = await db.get(User, customer.user_id) if customer else None
        sender_label = (cust_user.display_name if cust_user else None) or "ลูกค้า"
        staff_rows = (await db.exec(
            select(StaffMember).where(
                StaffMember.shop_id == thread.shop_id,
                StaffMember.user_id.is_not(None),
                StaffMember.revoked_at.is_(None),
            )
        )).all()
        push_url = f"/shop/messages/{thread.id}"
        for staff in staff_rows:
            staff_user = await db.get(User, staff.user_id)
            if staff_user is None:
                continue
            try:
                await send_to_user(
                    staff_user,
                    title=sender_label,
                    body=body_preview,
                    url=push_url,
                )
            except Exception:
                logger.exception("web_push to staff failed (shop=%s staff=%s)", thread.shop_id, staff.id)
    else:
        # Notify the customer's open thread page + bump the dock
        # badge. The dock counter merges DeeReach unread with chat
        # unread (single inbox surface), so add both before publish.
        publish_customer(thread.customer_id, "chat-message-in", json.dumps({
            "shop_id": str(thread.shop_id),
            "html": bubble_for_customer,
        }))
        chat_total = await customer_unread_total(db, thread.customer_id)
        deereach_total = (await db.exec(
            select(func.count()).select_from(Inbox).where(
                Inbox.customer_id == thread.customer_id,
                Inbox.read_at.is_(None),
            )
        )).one()
        merged = int(chat_total or 0) + int(deereach_total or 0)
        publish_customer(thread.customer_id, "inbox-update", str(merged))
        logger.info(
            f"💬 chat publish (shop→customer): customer={thread.customer_id} "
            f"shop={thread.shop_id} msg={msg.id} merged_unread={merged}"
        )
        # Web Push to the customer with the shop's name as the title.
        from app.models import Customer, Shop, User
        customer = await db.get(Customer, thread.customer_id)
        cust_user = await db.get(User, customer.user_id) if customer else None
        shop = await db.get(Shop, thread.shop_id)
        shop_label = (shop.name if shop else None) or "ร้าน"
        push_url = f"/messages/{thread.shop_id}"
        try:
            await send_to_user(
                cust_user,
                title=shop_label,
                body=body_preview,
                url=push_url,
            )
        except Exception:
            logger.exception("web_push to customer failed (customer=%s)", thread.customer_id)


async def list_messages(
    db: AsyncSession, thread_id: UUID, *, limit: int = 200
) -> List[CustomerMessage]:
    """Oldest-first message list, capped at `limit`. Phase 1 keeps
    the cap loose (200) since most threads will be small; pagination
    comes later if conversations get long."""
    rows = (await db.exec(
        select(CustomerMessage)
        .where(CustomerMessage.thread_id == thread_id)
        .order_by(CustomerMessage.created_at)
        .limit(limit)
    )).all()
    return list(rows)


async def list_threads_for_shop(
    db: AsyncSession, shop_id: UUID, *, limit: int = 200
) -> List[CustomerThread]:
    rows = (await db.exec(
        select(CustomerThread)
        .where(CustomerThread.shop_id == shop_id)
        .order_by(CustomerThread.last_at.desc())
        .limit(limit)
    )).all()
    return list(rows)


async def mark_read(
    db: AsyncSession, thread: CustomerThread, *, by: str
) -> None:
    """Zero the corresponding unread counter when a side opens the
    thread. `by` = 'customer' or 'shop'. Also re-publishes the dock
    badge so the badge dropping is reflected in real time on every
    other tab the user has open."""
    from app.services.events import publish, publish_customer

    if by == "customer" and thread.customer_unread:
        thread.customer_unread = 0
        db.add(thread)
        await db.commit()
        chat_total = await customer_unread_total(db, thread.customer_id)
        deereach_total = (await db.exec(
            select(func.count()).select_from(Inbox).where(
                Inbox.customer_id == thread.customer_id,
                Inbox.read_at.is_(None),
            )
        )).one()
        merged = int(chat_total or 0) + int(deereach_total or 0)
        publish_customer(thread.customer_id, "inbox-update", str(merged))
    elif by == "shop" and thread.shop_unread:
        thread.shop_unread = 0
        db.add(thread)
        await db.commit()
        new_total = await shop_unread_total(db, thread.shop_id)
        publish(thread.shop_id, "messages-update", str(new_total))


async def shop_unread_total(db: AsyncSession, shop_id: UUID) -> int:
    """Sum of shop_unread across all threads at this shop. Used by
    the dock badge."""
    rows = (await db.exec(
        select(CustomerThread.shop_unread).where(
            CustomerThread.shop_id == shop_id
        )
    )).all()
    return sum(int(r or 0) for r in rows)


async def customer_unread_total(
    db: AsyncSession, customer_id: UUID
) -> int:
    rows = (await db.exec(
        select(CustomerThread.customer_unread).where(
            CustomerThread.customer_id == customer_id
        )
    )).all()
    return sum(int(r or 0) for r in rows)


async def search_shops(
    db: AsyncSession, q: str, *, limit: int = 30
) -> List[Tuple[UUID, str, Optional[str]]]:
    """Case-insensitive substring search on Shop.name. Returns the
    minimal tuple needed to render a result row — keeps the rendered
    payload small even when the operator types a single character."""
    from app.models import Shop

    qstr = (q or "").strip()
    stmt = select(Shop.id, Shop.name, Shop.location)
    if qstr:
        stmt = stmt.where(Shop.name.ilike(f"%{qstr}%"))
    stmt = stmt.order_by(Shop.name).limit(limit)
    rows = (await db.exec(stmt)).all()
    return [(r[0], r[1], r[2]) for r in rows]
