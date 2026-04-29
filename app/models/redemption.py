from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from app.models.util import utcnow


class Redemption(SQLModel, table=True):
    """A customer claiming a reward. Groups the points that were consumed.

    Voiding sets `is_voided` — the points become available again.
    """

    __tablename__ = "redemptions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    shop_id: UUID = Field(foreign_key="shops.id", index=True)
    customer_id: UUID = Field(foreign_key="customers.id", index=True)
    branch_id: Optional[UUID] = Field(default=None, foreign_key="branches.id", index=True)

    is_voided: bool = Field(default=False)
    voided_at: Optional[datetime] = Field(default=None)
    voided_by_staff_id: Optional[UUID] = Field(default=None, foreign_key="staff_members.id")

    # When the shop actually fulfilled the voucher (served the free coffee).
    # Set by /shop/issue/scan when the staff scans the customer's QR within
    # the served-window after a redemption — flips the C5 voucher to a
    # greyed "✓ ใช้แล้ว HH:MM" state. Stays NULL for vouchers that were
    # never claimed in person (rare — only if customer redeemed but never
    # showed up).
    served_at: Optional[datetime] = Field(default=None)
    served_by_staff_id: Optional[UUID] = Field(default=None, foreign_key="staff_members.id")

    created_at: datetime = Field(default_factory=utcnow)

    points: List["Point"] = Relationship(back_populates="redemption")
    branch: Optional["Branch"] = Relationship(back_populates="redemptions")
