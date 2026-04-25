from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from app.models.util import utcnow


class Customer(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    is_anonymous: bool = Field(default=True)
    line_id: Optional[str] = Field(default=None, unique=True, index=True)
    phone: Optional[str] = Field(default=None, unique=True, index=True)
    display_name: Optional[str] = Field(default="Guest")
    created_at: datetime = Field(default_factory=utcnow)

    stamps: List["Stamp"] = Relationship(back_populates="customer")
