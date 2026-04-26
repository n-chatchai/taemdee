from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from app.models.util import utcnow


class Point(SQLModel, table=True):
    """A stamp/point a customer earned at a shop.

    Renamed from `Point` to align the data layer with the UI's "แต้ม" terminology.
    The underlying table is `point` (migration d8e7c2a91f50).
    """

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    shop_id: UUID = Field(foreign_key="shop.id", index=True)
    customer_id: UUID = Field(foreign_key="customer.id", index=True)
    # Null for single-branch shops (no branch to attribute to).
    branch_id: Optional[UUID] = Field(default=None, foreign_key="branch.id", index=True)

    # How this point was issued: customer_scan, shop_scan, phone_entry, or system (bonus/admin).
    issuance_method: str
    # Null for customer_scan (nobody clicked); set for shop_scan / phone_entry / system.
    issued_by_staff_id: Optional[UUID] = Field(default=None, foreign_key="staff_members.id")

    # Null until consumed by a redemption. A point whose redemption was voided is treated as
    # available again (see services/redemption.py).
    redemption_id: Optional[UUID] = Field(default=None, foreign_key="redemption.id", index=True)

    # Point-level void — for correcting a wrongly-issued point within the 60-sec window.
    is_voided: bool = Field(default=False)
    voided_at: Optional[datetime] = Field(default=None)
    voided_by_staff_id: Optional[UUID] = Field(default=None, foreign_key="staff_members.id")

    created_at: datetime = Field(default_factory=utcnow)

    shop: "Shop" = Relationship(back_populates="points")
    customer: "Customer" = Relationship(back_populates="points")
    branch: Optional["Branch"] = Relationship(back_populates="points")
    redemption: Optional["Redemption"] = Relationship(back_populates="points")
