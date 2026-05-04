from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from app.models.util import utcnow


class StaffMember(SQLModel, table=True):
    """A staff member at a shop — owner OR invited team member.

    Identity (line_id / google_id / facebook_id / phone / display_name
    / picture_url) lives on the linked User row. Read-through
    @property accessors below preserve `staff.<column>` for existing
    templates + services. Role-specific bits stay here: shop FK, owner
    flag, the four `can_*` permissions, and the invite lifecycle
    (invited_at / accepted_at / revoked_at + token).
    """

    __tablename__ = "staff_members"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    shop_id: UUID = Field(foreign_key="shops.id", index=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)

    # Owner is modelled as a StaffMember with is_owner=True. One owner
    # row per Shop, created at signup. Owners have all permissions
    # implicit and are NEVER revoked through the team UI.
    is_owner: bool = Field(default=False)

    # Permissions — "Issue stamps" is implicit. Owners short-circuit
    # every gate via is_owner; these flags only meaningfully gate
    # non-owner staff.
    can_void: bool = Field(default=True)
    can_deereach: bool = Field(default=False)
    can_topup: bool = Field(default=False)
    can_settings: bool = Field(default=False)

    invited_at: datetime = Field(default_factory=utcnow)
    accepted_at: Optional[datetime] = Field(default=None)
    revoked_at: Optional[datetime] = Field(default=None)

    # Short token in the join URL the staff scans/clicks. 24h TTL,
    # single-use (cleared on accept). Owner re-invites mint fresh.
    invite_token: Optional[str] = Field(default=None, unique=True, index=True)
    invite_token_expires_at: Optional[datetime] = Field(default=None)

    # ── Relationships ──────────────────────────────────────────────────────
    shop: "Shop" = Relationship(back_populates="staff_members")
    # lazy='joined' so the @property accessors below stay sync-safe in
    # Jinja templates — see the same rationale on Customer.user.
    user: "User" = Relationship(
        back_populates="staff_members",
        sa_relationship_kwargs={"lazy": "joined"},
    )

    # ── Compat accessors — read-through to self.user ───────────────────────
    @property
    def line_id(self) -> Optional[str]:
        return self.user.line_id if self.user else None

    @property
    def google_id(self) -> Optional[str]:
        return self.user.google_id if self.user else None

    @property
    def facebook_id(self) -> Optional[str]:
        return self.user.facebook_id if self.user else None

    @property
    def phone(self) -> Optional[str]:
        return self.user.phone if self.user else None

    @property
    def display_name(self) -> Optional[str]:
        return self.user.display_name if self.user else None

    @property
    def picture_url(self) -> Optional[str]:
        return self.user.picture_url if self.user else None
