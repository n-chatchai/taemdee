from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlmodel import Field, Relationship, SQLModel

from app.models.util import utcnow


class Shop(SQLModel, table=True):
    __tablename__ = "shops"

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

    # S5 issuance method toggles (multi-select per design 2026-04-26).
    # All four default ON so a freshly-onboarded shop accepts every
    # issuance path. Owner can disable any of them at /shop/issue/methods.
    # Disabling `customer_scan` makes the printed QR refuse new stamps
    # (used when a shop wants only staff-driven issuance — e.g. theft
    # or anti-bot regimes).
    issue_method_customer_scan: bool = Field(default=True)
    issue_method_shop_scan: bool = Field(default=True)
    issue_method_phone_entry: bool = Field(default=True)
    issue_method_grant: bool = Field(default=True)

    # Anti-rescan: minimum minutes between stamps from the same customer at this shop.
    # 0 = no cooldown (every scan succeeds). No UI yet — set via SQL/admin until S10
    # gains a control. Replaces the v1 hardcoded "1 stamp / customer / day" rule.
    scan_cooldown_minutes: int = Field(default=0)

    # Multi-branch: "shared" = one reward across all branches; "separate" = one reward per branch.
    # Locked after the 2nd branch is added (per PRD §6.I).
    reward_mode: str = Field(default="shared")


    theme_name: str = Field(default="taemdee")
    logo_url: Optional[str] = Field(default=None)

    # S10.location — split address. `location` holds the province (free text
    # but typically picked from a 77-province datalist on S2.1 / settings).
    # `district` and `address_detail` are added later via S10.location.
    location: Optional[str] = Field(default=None)  # province, e.g., "เชียงใหม่"
    district: Optional[str] = Field(default=None)
    address_detail: Optional[str] = Field(default=None)

    # S10.contact — public-facing shop phone (separate from the owner login
    # `phone` field above). `opening_hours` is a 7-key JSON map:
    #   { "mon": {"open": "07:00", "close": "18:00", "closed": false}, ... }
    # Days use 3-letter lowercase keys (mon/tue/wed/thu/fri/sat/sun).
    shop_phone: Optional[str] = Field(default=None)
    opening_hours: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=Column(JSON().with_variant(JSONB, "postgresql"), nullable=True),
    )

    # C9 Shop Story — emotional layer the customer sees from /card/{id}.
    # `thanks_message` is a short personal note ("ดีใจที่กลับมาทุกครั้ง · ทางร้านฝากบอก");
    # `story_text` is the longer "เรื่องราวของร้าน" paragraph. Both
    # nullable — the page hides the corresponding section when empty.
    thanks_message: Optional[str] = Field(default=None)
    story_text: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow)

    points: List["Point"] = Relationship(back_populates="shop")
    topup_slips: List["TopupSlip"] = Relationship(back_populates="shop")
    branches: List["Branch"] = Relationship(back_populates="shop")
    staff_members: List["StaffMember"] = Relationship(back_populates="shop")
