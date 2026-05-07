"""PWA login anchor lifecycle.

Anchors bridge the iOS-PWA / Safari cookie-jar split for shop
sign-in. See `app.models.pwa_login_anchor.PwaLoginAnchor` for the
why; this module is the lifecycle: create on /shop/login render,
claim in the OAuth callback, redeem at /auth/pwa-claim.
"""

from typing import Optional, Tuple
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import PwaLoginAnchor
from app.models.util import utcnow


async def create_anchor(db: AsyncSession) -> PwaLoginAnchor:
    """Insert a fresh anchor row (shop_id=NULL) and return it."""
    anchor = PwaLoginAnchor()
    db.add(anchor)
    await db.commit()
    await db.refresh(anchor)
    return anchor


async def get_active_anchor(
    db: AsyncSession, anchor_id: UUID
) -> Optional[PwaLoginAnchor]:
    """Return the anchor if it exists and isn't expired, else None."""
    anchor = await db.get(PwaLoginAnchor, anchor_id)
    if anchor is None:
        return None
    if anchor.expires_at < utcnow():
        return None
    return anchor


async def claim_anchor(
    db: AsyncSession,
    anchor_id: UUID,
    shop_id: UUID,
    staff_id: UUID,
    is_owner: bool,
) -> bool:
    """Mark the anchor as claimed by the resolved shop+staff. Returns
    True on success, False if the anchor doesn't exist or is expired.
    Idempotent: re-claiming the same anchor with the same identity is
    a no-op.
    """
    anchor = await get_active_anchor(db, anchor_id)
    if anchor is None:
        return False
    anchor.shop_id = shop_id
    anchor.staff_id = staff_id
    anchor.is_owner = is_owner
    anchor.claimed_at = utcnow()
    db.add(anchor)
    await db.commit()
    return True


async def redeem_anchor(
    db: AsyncSession, anchor_id: UUID
) -> Optional[Tuple[UUID, UUID, bool]]:
    """Single-use redemption: returns (shop_id, staff_id, is_owner)
    if the anchor is claimed, then deletes the row. Returns None when
    the anchor is missing, expired, or still pending. Caller mints the
    real session cookie on success.
    """
    anchor = await get_active_anchor(db, anchor_id)
    if anchor is None or anchor.shop_id is None or anchor.staff_id is None:
        return None
    triple = (anchor.shop_id, anchor.staff_id, anchor.is_owner)
    await db.delete(anchor)
    await db.commit()
    return triple


async def cleanup_expired(db: AsyncSession) -> int:
    """Delete expired anchor rows. Returns the count removed. Wire
    this into the existing scheduled cleanup if/when one exists; for
    now it's safe to leave rows lying around because nothing reads
    them past expires_at.
    """
    stmt = select(PwaLoginAnchor).where(PwaLoginAnchor.expires_at < utcnow())
    rows = (await db.execute(stmt)).scalars().all()
    for row in rows:
        await db.delete(row)
    await db.commit()
    return len(rows)
