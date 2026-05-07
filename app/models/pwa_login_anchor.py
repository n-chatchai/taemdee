from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

from app.models.util import utcnow


def _default_expires_at() -> datetime:
    return utcnow() + timedelta(hours=1)


class PwaLoginAnchor(SQLModel, table=True):
    """Pre-OAuth identity anchor for the shop side, mirroring how
    `customers` rows act as a stable identity for customer-side OAuth.

    iOS standalone PWA installs have a cookie jar separate from Safari
    (iOS 16.4+). Shop owners signing in via LINE/Google/FB get pushed
    out to Safari for OAuth, the session cookie minted in the callback
    lives in Safari, and the PWA never sees it. Customer flow sidesteps
    this because the PWA already holds an anon `customer_cookie` from
    the first scan — the OAuth callback updates the customers row by
    id (carried in the state JWT), and the PWA's existing cookie still
    points at the now-claimed row.

    For shop, no equivalent pre-existing identity exists: a `Shop` row
    is only created at OAuth time. This anchor table fills the same
    slot — PWA gets a `shop_pwa_anchor` cookie when /shop/login
    renders, the cookie maps to a row here (initially shop_id=NULL),
    state JWT carries the anchor id through OAuth, and the callback
    UPDATEs this row with the resolved shop+staff. PWA's
    visibilitychange handler POSTs /auth/pwa-claim, which mints a real
    session cookie when the row has been claimed. The anchor row is
    deleted on successful claim — single-use.
    """

    __tablename__ = "pwa_login_anchors"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # NULL until the OAuth callback claims the anchor.
    shop_id: Optional[UUID] = Field(default=None, foreign_key="shops.id", index=True)
    staff_id: Optional[UUID] = Field(default=None, foreign_key="staff_members.id")
    is_owner: bool = Field(default=False)

    created_at: datetime = Field(default_factory=utcnow)
    claimed_at: Optional[datetime] = Field(default=None)

    # 1-hour TTL — the OAuth round-trip plus user-app-switch latency
    # is well under this. Stale rows are cleaned up by an out-of-band
    # job; expired rows are also rejected at claim time.
    expires_at: datetime = Field(default_factory=_default_expires_at, index=True)
