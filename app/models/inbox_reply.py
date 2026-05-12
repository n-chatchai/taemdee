from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

from app.models.util import utcnow


class InboxReply(SQLModel, table=True):
    """Reply attached to an Inbox row (a DeeReach broadcast that landed in
    the customer's inbox). Replaces the old CustomerThread / CustomerMessage
    chat model — per design, replies are scoped to the broadcast that
    triggered them, not a free-form thread.

    Both sides reply for free (no credit charge); the credit cost is
    only on the broadcast itself.

    `sender` is "customer" or "shop". The implicit identity comes from
    the parent Inbox row (Inbox.customer_id, Inbox.shop_id), so we don't
    carry per-reply user FKs.

    Read state is denormalised onto Inbox: the customer's read_at sits
    on the Inbox row itself (already there), and the shop's unread is
    derived by counting customer-sender replies whose `shop_read_at` is
    NULL."""

    __tablename__ = "inbox_replies"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    inbox_id: UUID = Field(foreign_key="inboxes.id", index=True)

    sender: str = Field(index=True)  # 'customer' | 'shop'
    body: str = Field(default="")

    # Origin of the reply — defaults to 'app' (in-app POST on
    # /my-inbox/<id>/reply or /shop/messages/<id>/reply). 'line' is
    # set by the LINE Messaging API webhook when a customer reply
    # arrives via the @taemdee OA chat. The shop-side thread surfaces
    # a small "ผ่านไลน์" pill on non-'app' rows so the operator
    # knows the customer used the external channel.
    source: str = Field(default="app", index=True)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    # Set when the shop opens the broadcast detail page; only meaningful
    # for sender='customer' rows. Lets the shop list show an unread badge
    # per broadcast without scanning the whole reply table on render.
    shop_read_at: Optional[datetime] = Field(default=None)
