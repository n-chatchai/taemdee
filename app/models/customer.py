from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from app.models.util import utcnow


class CustomerShopMute(SQLModel, table=True):
    __tablename__ = "customer_shop_mutes"
    customer_id: UUID = Field(foreign_key="customers.id", primary_key=True)
    shop_id: UUID = Field(foreign_key="shops.id", primary_key=True)
    preferred_channel: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)


class Customer(SQLModel, table=True):
    __tablename__ = "customers"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    is_anonymous: bool = Field(default=True)
    line_id: Optional[str] = Field(default=None, unique=True, index=True)
    google_id: Optional[str] = Field(default=None, unique=True, index=True)
    facebook_id: Optional[str] = Field(default=None, unique=True, index=True)
    phone: Optional[str] = Field(default=None, unique=True, index=True)
    # NULL = "haven't been asked yet" — the welcome sheet auto-opens until
    # the customer either provides a nickname or skips (skip stores the
    # polite default "คุณลูกค้า" so we don't ask again).
    display_name: Optional[str] = Field(default=None)
    # Master DeeReach kill-switch — when False, _audience_for filters
    # this customer out of every campaign kind across every shop. Per-
    # shop mute (CustomerShopMute) still works alongside; the master
    # toggle is just the global override the customer can flip from
    # settings.notif. Default True so existing rows stay opted in.
    notifications_enabled: bool = Field(default=True)
    preferred_channel: Optional[str] = Field(default=None)
    # Web Push (VAPID) subscription. Set when the customer accepts the
    # browser push prompt — endpoint URL is the per-browser push service
    # endpoint, p256dh + auth are the client keys we encrypt the payload
    # with. All NULL = not subscribed; waterfall falls through to LINE/SMS/inbox.
    web_push_endpoint: Optional[str] = Field(default=None)
    web_push_p256dh: Optional[str] = Field(default=None)
    web_push_auth: Optional[str] = Field(default=None)
    # C2.4 recovery code — issued when an anonymous customer skips signup so
    # they can re-claim their points on a new device. 12 digits in
    # 3 groups of 4 (e.g. "1234-5678-9012"). Stored hyphen-included.
    recovery_code: Optional[str] = Field(default=None, unique=True, index=True)

    # link.prompt cooldown — set when a still-anonymous customer taps "ไว้ก่อน"
    # on the soft prompt that appears once they've collected ≥3 stamps. Re-show
    # only after 14 days. NULL = never snoozed (eligible to show as soon as
    # the stamp threshold is met).
    last_link_prompt_snoozed_at: Optional[datetime] = Field(default=None)

    # C6 accessibility — "ขนาดตัวอักษร" picker. Values: "sm" | "md" | "lg".
    # NULL = "md" (default, no zoom). Drives a `.ts-{value}` class on
    # <html> via the pwa_head bootstrap script (also persisted in
    # localStorage for instant first-paint without a round-trip).
    text_size: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)

    points: List["Point"] = Relationship(back_populates="customer")
