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
    """Customer profile — points + redemption side of the platform.

    Identity (line_id, google_id, facebook_id, phone, display_name,
    picture_url, recovery_code, line_friend_status, web_push_*, is_pwa,
    text_size, notifications_enabled) lives on the linked User row,
    not here. Read-through @property accessors below preserve the
    `customer.<column>` names that templates and services already use.

    Role-specific fields stay: anonymous flag, preferred channel, the
    one-shot link-prompt snooze.
    """

    __tablename__ = "customers"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)

    is_anonymous: bool = Field(default=True)
    preferred_channel: Optional[str] = Field(default=None)

    # link.prompt cooldown — set when a still-anonymous customer taps "ไว้ก่อน"
    # on the soft prompt that appears once they've collected ≥3 stamps.
    last_link_prompt_snoozed_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow)

    # ── Relationships ──────────────────────────────────────────────────────
    # lazy='joined' so the read-through @property accessors below can run
    # under sync template rendering without re-entering the async pool
    # for an N+1 fetch (sa_async would raise MissingGreenlet).
    user: "User" = Relationship(
        back_populates="customers",
        sa_relationship_kwargs={"lazy": "joined"},
    )
    points: List["Point"] = Relationship(back_populates="customer")

    # ── Compat accessors — read-through to self.user ───────────────────────
    # Lets every existing `customer.line_id` / `.display_name` / etc.
    # callsite keep working without touching templates + services. New
    # writes should target customer.user.<col> directly.
    @property
    def line_id(self) -> Optional[str]:
        return self.user.line_id if self.user else None

    @property
    def google_id(self) -> Optional[str]:
        return self.user.google_id if self.user else None

    @property
    def facebook_id(self) -> Optional[str]:
        return self.user.facebook_id if self.user else None

    @property
    def phone(self) -> Optional[str]:
        return self.user.phone if self.user else None

    @property
    def display_name(self) -> Optional[str]:
        return self.user.display_name if self.user else None

    @property
    def picture_url(self) -> Optional[str]:
        return self.user.picture_url if self.user else None

    @property
    def recovery_code(self) -> Optional[str]:
        return self.user.recovery_code if self.user else None

    @property
    def line_friend_status(self) -> Optional[str]:
        return self.user.line_friend_status if self.user else None

    @property
    def line_messaging_blocked_at(self) -> Optional[datetime]:
        return self.user.line_messaging_blocked_at if self.user else None

    @property
    def is_pwa(self) -> bool:
        return bool(self.user and self.user.is_pwa)

    @property
    def text_size(self) -> Optional[str]:
        return self.user.text_size if self.user else None

    @property
    def notifications_enabled(self) -> bool:
        return bool(self.user and self.user.notifications_enabled)

    @property
    def web_push_endpoint(self) -> Optional[str]:
        return self.user.web_push_endpoint if self.user else None

    @property
    def web_push_p256dh(self) -> Optional[str]:
        return self.user.web_push_p256dh if self.user else None

    @property
    def web_push_auth(self) -> Optional[str]:
        return self.user.web_push_auth if self.user else None
