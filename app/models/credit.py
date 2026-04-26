from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from app.models.util import utcnow


class TopupSlip(SQLModel, table=True):
    __tablename__ = "topup_slips"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    shop_id: UUID = Field(foreign_key="shop.id")
    amount: int
    slip_image_url: str
    slip_hash: str = Field(unique=True)  # Prevent double usage of same slip
    status: str = Field(default="pending")  # pending, verified, rejected
    verified_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)

    shop: "Shop" = Relationship(back_populates="topup_slips")


class CreditLog(SQLModel, table=True):
    __tablename__ = "credit_logs"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    shop_id: UUID = Field(foreign_key="shop.id")
    amount: int  # Positive for topup, negative for deduction
    reason: str  # "deereach_send", "topup", "correction"
    related_id: Optional[UUID] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
