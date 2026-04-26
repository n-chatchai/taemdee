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
    # One of: "coffee_cup", "latte_art", "iced", or None (custom upload, future). The
    # picker on S2.2 maps to a CSS-drawn icon shown in the customer DeeCard reward pill.
    reward_image: Optional[str] = Field(default="coffee_cup")
    # Legacy single-value field — superseded by the four method toggles below.
    # Kept nullable for back-compat with existing rows; no longer read at runtime.
    issuance_method: str = Field(default="all")

    # S5 issuance method toggles (multi-select per design 2026-04-26):
    # `customer_scan` is implicit — every shop has a printable QR; not stored.
    # The other three default ON so the FAB sheet is useful from day one
    # (matches the S3.choose mockup with all 3 methods listed). Owner can
    # disable individual methods at /shop/issue.
    issue_method_shop_scan: bool = Field(default=True)
    issue_method_phone_entry: bool = Field(default=True)
    issue_method_search: bool = Field(default=True)

    # Anti-rescan: minimum minutes between stamps from the same customer at this shop.
    # 0 = no cooldown (every scan succeeds). No UI yet — set via SQL/admin until S10
    # gains a control. Replaces the v1 hardcoded "1 stamp / customer / day" rule.
    scan_cooldown_minutes: int = Field(default=0)

    # Multi-branch: "shared" = one reward across all branches; "separate" = one reward per branch.
    # Locked after the 2nd branch is added (per PRD §6.I).
    reward_mode: str = Field(default="shared")

    # 4-digit PIN staff types on a customer's phone to confirm redemption. Null = unset.
    shop_pin: Optional[str] = Field(default=None)

    theme_name: str = Field(default="taemdee")
    logo_url: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None)  # e.g., "เชียงใหม่"

    created_at: datetime = Field(default_factory=utcnow)

    points: List["Point"] = Relationship(back_populates="shop")
    topup_slips: List["TopupSlip"] = Relationship(back_populates="shop")
    branches: List["Branch"] = Relationship(back_populates="shop")
    staff_members: List["StaffMember"] = Relationship(back_populates="shop")
