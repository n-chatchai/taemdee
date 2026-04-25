from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from app.models.util import utcnow


class Shop(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str
    phone: Optional[str] = Field(default=None, unique=True, index=True)
    line_id: Optional[str] = Field(default=None, unique=True, index=True)
    owner_email: Optional[str] = Field(default=None, unique=True, index=True)

    is_onboarded: bool = Field(default=False)

    credit_balance: int = Field(default=0)
    reward_threshold: int = Field(default=10)
    reward_description: str = Field(default="Free Coffee")
    issuance_method: str = Field(default="all")  # customer_scan, shop_scan, phone_entry, or "all"

    # Multi-branch: "shared" = one reward across all branches; "separate" = one reward per branch.
    # Locked after the 2nd branch is added (per PRD §6.I).
    reward_mode: str = Field(default="shared")

    # 4-digit PIN staff types on a customer's phone to confirm redemption. Null = unset.
    shop_pin: Optional[str] = Field(default=None)

    theme_name: str = Field(default="taemdee")
    logo_url: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow)

    stamps: List["Stamp"] = Relationship(back_populates="shop")
    topup_slips: List["TopupSlip"] = Relationship(back_populates="shop")
    branches: List["Branch"] = Relationship(back_populates="shop")
    staff_members: List["StaffMember"] = Relationship(back_populates="shop")
