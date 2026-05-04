"""PWA OAuth pairing handoff.

When a customer taps "ผูกบัญชี LINE" inside the PWA, OAuth runs in the
external browser (Safari, Chrome, etc.) — that browser's cookie store is
disjoint from the PWA's. A `Pairing` row is the server-side rendezvous
that lets the PWA pick up the resulting customer session without
relying on cross-browser cookie magic.

See docs/pwa-oauth-pairing.md for the full flow.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

from app.models.util import utcnow


class Pairing(SQLModel, table=True):
    """One row per pairing attempt. Created by /auth/pair/start, claimed by
    the OAuth callback, redeemed by the PWA after SSE notification.

    `code` is the public identifier (in the OAuth URL); `pwa_token` is the
    server-issued cookie value bound to the PWA that started the pairing.
    Redeem requires both, so a leaked `code` can't be used from another
    browser context.
    """
    __tablename__ = "pwa_token_pairings"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    code: str = Field(unique=True, index=True, max_length=64)
    pwa_token: str = Field(max_length=64)
    # Set by /auth/pair/start when the PWA already has a customer cookie.
    # The OAuth callback uses this to bind the new provider to the *same*
    # customer (and User) instead of a cookie-less anonymous row that
    # would otherwise spawn a parallel User with only the new identity.
    originator_customer_id: Optional[UUID] = Field(
        default=None, foreign_key="customers.id"
    )
    customer_id: Optional[UUID] = Field(default=None, foreign_key="customers.id")
    provider: Optional[str] = Field(default=None, max_length=32)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    redeemed_at: Optional[datetime] = Field(default=None)
