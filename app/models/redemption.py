from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from app.models.util import utcnow


class Redemption(SQLModel, table=True):
    """A customer claiming a reward. Groups the points that were consumed.

    Voiding sets `is_voided` — the points become available again.
    """

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    shop_id: UUID = Field(foreign_key="shop.id", index=True)
    customer_id: UUID = Field(foreign_key="customer.id", index=True)
    branch_id: Optional[UUID] = Field(default=None, foreign_key="branch.id", index=True)

    is_voided: bool = Field(default=False)
    voided_at: Optional[datetime] = Field(default=None)
    voided_by_staff_id: Optional[UUID] = Field(default=None, foreign_key="staff_members.id")

    created_at: datetime = Field(default_factory=utcnow)

    points: List["Point"] = Relationship(back_populates="redemption")
    branch: Optional["Branch"] = Relationship(back_populates="redemptions")
