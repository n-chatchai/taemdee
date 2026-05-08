from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from app.models.util import utcnow


class CustomerThread(SQLModel, table=True):
    """Customer ↔ Shop conversation. One row per (customer, shop) pair —
    re-opening a "new" message just appends to the existing thread.
    Unread counts are denormalized to keep the list view cheap; they're
    incremented when the other side sends and zeroed when this side
    opens the thread."""

    __tablename__ = "customer_threads"
    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # The (customer, shop) pair is the natural key — UNIQUE constraint
    # enforced by the migration's index. Use it from get_or_create.
    customer_id: UUID = Field(foreign_key="customers.id", index=True)
    shop_id: UUID = Field(foreign_key="shops.id", index=True)

    last_at: datetime = Field(default_factory=utcnow, index=True)
    customer_unread: int = Field(default=0)
    shop_unread: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow)


class CustomerMessage(SQLModel, table=True):
    """Single message inside a CustomerThread. `sender` is "customer" or
    "shop"; the actor's id (customer_id from the thread for customer
    messages, staff_id for shop messages) lives implicit on the thread
    relationship — we don't need a per-message user FK because the
    permissions gate is "you can read this thread iff you're the
    thread's customer / a staff member of the thread's shop"."""

    __tablename__ = "customer_messages"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    thread_id: UUID = Field(foreign_key="customer_threads.id", index=True)
    sender: str = Field(index=True)  # 'customer' | 'shop'
    body: str = Field(default="")

    # R2-hosted media — nullable for text-only messages. Phase 3 wires
    # this up properly with content-type sniffing + size limits.
    attachment_url: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    read_at: Optional[datetime] = Field(default=None)
