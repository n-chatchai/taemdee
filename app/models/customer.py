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
    phone: Optional[str] = Field(default=None, unique=True, index=True)
    # NULL = "haven't been asked yet" — the welcome sheet auto-opens until
    # the customer either provides a nickname or skips (skip stores the
    # polite default "คุณลูกค้า" so we don't ask again).
    display_name: Optional[str] = Field(default=None)
    preferred_channel: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)

    points: List["Point"] = Relationship(back_populates="customer")
