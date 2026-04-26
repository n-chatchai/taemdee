"""Offer — a promise (system→shop, shop→customer, …) redeemable later as
stamps, credits, items, or gifts. See PRD §13."""

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

from app.models.util import utcnow


class Offer(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Source — who's giving. Nullable depending on type.
    source_type: str  # "system" | "shop" | "customer"
    source_shop_id: Optional[UUID] = Field(default=None, foreign_key="shop.id", index=True)
    source_customer_id: Optional[UUID] = Field(default=None, foreign_key="customer.id")

    # Target — who's receiving.
    target_type: str  # "shop" | "customer"
    target_shop_id: Optional[UUID] = Field(default=None, foreign_key="shop.id", index=True)
    target_customer_id: Optional[UUID] = Field(default=None, foreign_key="customer.id", index=True)

    # Discriminator. v1 kinds:
    #   credit_grant  — adds credits to a Shop's balance
    #   free_stamp    — adds a single bonus stamp on next visit
    #   bonus_stamp_count — adds N stamps now
    #   free_item     — show as banner on DeeCard with item description
    kind: str

    amount: Optional[int] = Field(default=None)
    description: Optional[str] = Field(default=None)

    valid_from: datetime = Field(default_factory=utcnow)
    valid_until: Optional[datetime] = Field(default=None)
    max_uses: int = Field(default=1)
    used_count: int = Field(default=0)
    status: str = Field(default="active")  # active | redeemed | expired | revoked

    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: Optional[datetime] = Field(default=None)


class Referral(SQLModel, table=True):
    """Shop → Shop referral (PRD §14, v1 only direction).

    Referrer generates a code; referee signs up via /shop/login?ref=<code>.
    On referee onboarding completion, both parties receive a credit_grant Offer.
    """

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    referrer_shop_id: UUID = Field(foreign_key="shop.id", index=True)
    referee_shop_id: Optional[UUID] = Field(default=None, foreign_key="shop.id", index=True)
    code: str = Field(unique=True, index=True)
    completed_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
