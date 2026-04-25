from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from app.models.util import utcnow


class Stamp(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    shop_id: UUID = Field(foreign_key="shop.id", index=True)
    customer_id: UUID = Field(foreign_key="customer.id", index=True)
    # Null for single-branch shops (no branch to attribute to).
    branch_id: Optional[UUID] = Field(default=None, foreign_key="branch.id", index=True)

    # How this stamp was issued: customer_scan, shop_scan, phone_entry, or system (bonus/admin).
    issuance_method: str
    # Null for customer_scan (nobody clicked); set for shop_scan / phone_entry / system.
    issued_by_staff_id: Optional[UUID] = Field(default=None, foreign_key="staffmember.id")

    # Null until consumed by a redemption. A stamp whose redemption was voided is treated as
    # available again (see services/redemption.py).
    redemption_id: Optional[UUID] = Field(default=None, foreign_key="redemption.id", index=True)

    # Stamp-level void — for correcting a wrongly-issued stamp within the 60-sec window.
    is_voided: bool = Field(default=False)
    voided_at: Optional[datetime] = Field(default=None)
    voided_by_staff_id: Optional[UUID] = Field(default=None, foreign_key="staffmember.id")

    created_at: datetime = Field(default_factory=utcnow)

    shop: "Shop" = Relationship(back_populates="stamps")
    customer: "Customer" = Relationship(back_populates="stamps")
    branch: Optional["Branch"] = Relationship(back_populates="stamps")
    redemption: Optional["Redemption"] = Relationship(back_populates="stamps")
