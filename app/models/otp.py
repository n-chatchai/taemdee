from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

from app.models.util import utcnow


class OtpCode(SQLModel, table=True):
    """A one-time code sent to a phone for login.

    In development the code is printed to stdout. In production a real SMS provider sends it.
    """

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    phone: str = Field(index=True)
    code: str
    expires_at: datetime
    consumed_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
