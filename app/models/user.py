"""Canonical identity for a person across roles.

A User has at most one Customer profile (their stamps + redemption
side) and 0..N StaffMember profiles (one per shop they work at).
Provider columns are unique — the same LINE / Google / Facebook /
phone can only sit on one User. When OAuth resolution finds a
provider id on a different User than the active session, the two
merge into the active session (services/identity.merge_users) so
"sign in here, see all your stuff" Just Works without manual
reconciliation.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

from sqlmodel import Field, Relationship, SQLModel

from app.models.util import utcnow


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # ── Provider identity ──────────────────────────────────────────────────
    # All four are unique across users; binding a provider that's
    # already on another User triggers a merge into the active row.
    line_id: Optional[str] = Field(default=None, unique=True, index=True)
    google_id: Optional[str] = Field(default=None, unique=True, index=True)
    facebook_id: Optional[str] = Field(default=None, unique=True, index=True)
    phone: Optional[str] = Field(default=None, unique=True, index=True)

    # ── Display / profile ──────────────────────────────────────────────────
    display_name: Optional[str] = Field(default=None)
    picture_url: Optional[str] = Field(default=None)

    # ── Recovery (for anonymous → claimed soft-wall flow) ──────────────────
    recovery_code: Optional[str] = Field(default=None, unique=True, index=True)

    # ── Username + 6-digit PIN (shop-side login channel) ───────────────────
    # Globally unique username + bcrypt-hashed PIN. Login UI is gated
    # to /staff/pin-login (shop side); customer side is connect-only.
    # Optional — users without an OAuth provider can still sign in
    # with these credentials, useful for shop staff on shared kiosks.
    username: Optional[str] = Field(default=None, unique=True, index=True)
    pin_hash: Optional[str] = Field(default=None)

    # ── LINE Messaging state ───────────────────────────────────────────────
    # NULL = unknown (haven't asked LINE yet); 'friended' = the OA's
    # webhook reported a follow; 'unfollowed' = unfollow event OR a
    # 403 from a push attempt. DeeReach `line` reachability requires
    # status != 'unfollowed' (NULL is treated as "maybe — try once").
    line_friend_status: Optional[str] = Field(default=None)
    line_messaging_blocked_at: Optional[datetime] = Field(default=None)

    # ── Person-level UX state ──────────────────────────────────────────────
    # Same person, same answer everywhere — these belong on the
    # identity, not a per-role profile.
    is_pwa: bool = Field(default=False)
    text_size: Optional[str] = Field(default=None)
    notifications_enabled: bool = Field(default=True)

    # ── Web Push subscription (per-browser, person-level) ──────────────────
    web_push_endpoint: Optional[str] = Field(default=None)
    web_push_p256dh: Optional[str] = Field(default=None)
    web_push_auth: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow)

    # ── Relationships ──────────────────────────────────────────────────────
    customers: List["Customer"] = Relationship(back_populates="user")
    staff_members: List["StaffMember"] = Relationship(back_populates="user")
