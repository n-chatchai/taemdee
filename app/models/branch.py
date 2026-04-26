from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from app.models.util import utcnow


class Branch(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    shop_id: UUID = Field(foreign_key="shop.id", index=True)
    name: str
    address: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)

    shop: "Shop" = Relationship(back_populates="branches")
    points: List["Point"] = Relationship(back_populates="branch")
    redemptions: List["Redemption"] = Relationship(back_populates="branch")
