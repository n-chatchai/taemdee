"""Customer ↔ Shop chat lifecycle.

One CustomerThread per (customer, shop) pair (UNIQUE in the DB).
`send_message` enforces a per-customer-per-shop rate limit; the
shop side has no rate limit (replying is the desired behaviour).
"""

from datetime import timedelta
from typing import List, Optional, Tuple
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import CustomerMessage, CustomerThread
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
    return msg


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


async def list_threads_for_customer(
    db: AsyncSession, customer_id: UUID, *, limit: int = 200
) -> List[CustomerThread]:
    rows = (await db.exec(
        select(CustomerThread)
        .where(CustomerThread.customer_id == customer_id)
        .order_by(CustomerThread.last_at.desc())
        .limit(limit)
    )).all()
    return list(rows)


async def mark_read(
    db: AsyncSession, thread: CustomerThread, *, by: str
) -> None:
    """Zero the corresponding unread counter when a side opens the
    thread. `by` = 'customer' or 'shop'."""
    if by == "customer" and thread.customer_unread:
        thread.customer_unread = 0
        db.add(thread)
        await db.commit()
    elif by == "shop" and thread.shop_unread:
        thread.shop_unread = 0
        db.add(thread)
        await db.commit()


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
