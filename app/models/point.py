from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Index, Relationship, SQLModel

from app.models.util import utcnow


class Point(SQLModel, table=True):
    """A point a customer earned at a shop."""

    __tablename__ = "points"
    __table_args__ = (
        Index("ix_points_shop_customer_active", "shop_id", "customer_id", "is_voided", "redemption_id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    shop_id: UUID = Field(foreign_key="shops.id", index=True)
    customer_id: UUID = Field(foreign_key="customers.id", index=True)
    # Null for single-branch shops (no branch to attribute to).
    branch_id: Optional[UUID] = Field(default=None, foreign_key="branches.id", index=True)

    # How this point was issued: customer_scan, shop_scan, phone_entry, or system (bonus/admin).
    issuance_method: str
    # Null for customer_scan (nobody clicked); set for shop_scan / phone_entry / system.
    issued_by_staff_id: Optional[UUID] = Field(default=None, foreign_key="staff_members.id")
    # Links points issued in a single manual grant action.
    grant_id: Optional[UUID] = Field(default=None, index=True)

    # Null until consumed by a redemption. A point whose redemption was voided is treated as
    # available again (see services/redemption.py).
    redemption_id: Optional[UUID] = Field(default=None, foreign_key="redemptions.id", index=True)

    # Point-level void — for correcting a wrongly-issued point within the 60-sec window.
    is_voided: bool = Field(default=False)
    voided_at: Optional[datetime] = Field(default=None)
    voided_by_staff_id: Optional[UUID] = Field(default=None, foreign_key="staff_members.id")

    created_at: datetime = Field(default_factory=utcnow)

    shop: "Shop" = Relationship(back_populates="points")
    customer: "Customer" = Relationship(back_populates="points")
    branch: Optional["Branch"] = Relationship(back_populates="points")
    redemption: Optional["Redemption"] = Relationship(back_populates="points")
