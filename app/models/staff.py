from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from app.models.util import utcnow


class StaffMember(SQLModel, table=True):
    """A staff member at a shop. Permissions are set by the owner at invite time."""

    __tablename__ = "staff_members"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    shop_id: UUID = Field(foreign_key="shops.id", index=True)

    phone: Optional[str] = Field(default=None, index=True)
    line_id: Optional[str] = Field(default=None, index=True)
    display_name: Optional[str] = Field(default=None)
    picture_url: Optional[str] = Field(default=None)

    # Permissions — "Issue stamps" is implicit (being invited means you can issue).
    can_void: bool = Field(default=True)
    can_deereach: bool = Field(default=False)
    can_topup: bool = Field(default=False)
    can_settings: bool = Field(default=False)

    invited_at: datetime = Field(default_factory=utcnow)
    accepted_at: Optional[datetime] = Field(default=None)
    revoked_at: Optional[datetime] = Field(default=None)

    # S-staff.invite — short token in the join URL the staff scans/clicks.
    # 24h TTL, single-use (cleared on accept). Owner can re-invite to mint
    # a fresh token after expiry.
    invite_token: Optional[str] = Field(default=None, unique=True, index=True)
    invite_token_expires_at: Optional[datetime] = Field(default=None)

    shop: "Shop" = Relationship(back_populates="staff_members")
