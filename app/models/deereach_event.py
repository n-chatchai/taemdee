"""Engagement events for DeeReach broadcasts.

One row per recorded interaction a customer has with a broadcast.
Foundation for the campaign stats page (aggregate counts), customer
engagement score (recency × frequency × variety), and future per-event
timeline view.

Why a separate table rather than denormalising onto Inbox / InboxReply:
  · A single broadcast can be opened multiple times (e.g. once at the
    push notification + once later from the dock). Inbox.read_at can
    only carry the first-open timestamp; an event log lets us count
    re-opens and reason about engagement intensity.
  · Replies already exist on InboxReply but we mirror them as events
    too so stats queries hit one homogeneous table instead of
    UNION-ing across tables.
  · Future event kinds (voucher_claimed, story_visited, etc.) plug in
    by appending to the `kind` enum without schema churn.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

from app.models.util import utcnow


class DeeReachEvent(SQLModel, table=True):
    """Engagement event tied to a broadcast inbox row.

    Always carries (customer_id, shop_id, inbox_id) so per-customer +
    per-shop + per-broadcast aggregates all hit the same indexes.
    `campaign_id` is optional because manual sends sometimes lack a
    campaign row (lets us still log the event even when there's no
    parent campaign to attribute to).

    `kind` is a free-form string ('opened' | 'replied' |
    'voucher_claimed' for now) instead of a Python enum so adding a
    new event type doesn't need a migration. Caller is responsible
    for using stable lowercase values — there's no DB CHECK.

    `payload` stays NULL by default; reply events fill in
    `{"reply_id": "..."}` so the timeline can deep-link, and future
    kinds can carry whatever they need without another column.
    """

    __tablename__ = "deereach_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    inbox_id: UUID = Field(foreign_key="inboxes.id", index=True)
    customer_id: UUID = Field(foreign_key="customers.id", index=True)
    shop_id: UUID = Field(foreign_key="shops.id", index=True)
    campaign_id: Optional[UUID] = Field(
        default=None, foreign_key="deereach_campaigns.id", index=True,
    )

    kind: str = Field(index=True)
    payload: Optional[str] = Field(default=None)  # JSON-as-string; null on simple events

    created_at: datetime = Field(default_factory=utcnow, index=True)
