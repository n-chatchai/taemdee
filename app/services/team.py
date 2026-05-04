"""Team (staff) management — invite, accept, update permissions, revoke."""

import secrets
from datetime import timedelta
from typing import List, Optional
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Shop, StaffMember
from app.models.util import utcnow

VALID_PERMISSIONS = {"can_void", "can_deereach", "can_topup", "can_settings"}
INVITE_TOKEN_TTL_HOURS = 24


def _generate_invite_token() -> str:
    """24 url-safe chars · ~144 bits entropy · single-use, 24h TTL."""
    return secrets.token_urlsafe(18)


async def mint_invite_token(db: AsyncSession, staff: StaffMember) -> str:
    """Refresh the invite token + expiry. Idempotent — calling twice replaces
    the previous token (old QR/link stops working, owner shares the new
    one). Used by the S-staff.invite re-share flow."""
    staff.invite_token = _generate_invite_token()
    staff.invite_token_expires_at = utcnow() + timedelta(hours=INVITE_TOKEN_TTL_HOURS)
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff.invite_token


async def find_pending_by_token(db: AsyncSession, token: str) -> Optional[StaffMember]:
    """Resolve the Staff.join URL token → pending staff member. Returns
    None for expired / revoked / already-accepted / unknown tokens so
    the join page can render a friendly 'invite expired' state."""
    if not token:
        return None
    row = (await db.exec(
        select(StaffMember).where(StaffMember.invite_token == token)
    )).first()
    if row is None:
        return None
    if row.revoked_at is not None or row.accepted_at is not None:
        return None
    if row.invite_token_expires_at and row.invite_token_expires_at < utcnow():
        return None
    return row


async def invite_staff(
    db: AsyncSession,
    shop: Shop,
    *,
    phone: Optional[str] = None,
    line_id: Optional[str] = None,
    display_name: Optional[str] = None,
    can_void: bool = True,
    can_deereach: bool = False,
    can_topup: bool = False,
    can_settings: bool = False,
) -> StaffMember:
    """Create a pending StaffMember. The invitee accepts by logging in with the matching
    phone or LINE id (see `accept_invite`)."""
    if not phone and not line_id:
        raise ValueError("Staff invite requires phone or line_id")

    staff = StaffMember(
        shop_id=shop.id,
        phone=phone,
        line_id=line_id,
        display_name=display_name,
        can_void=can_void,
        can_deereach=can_deereach,
        can_topup=can_topup,
        can_settings=can_settings,
    )
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff


async def accept_invite(db: AsyncSession, staff: StaffMember) -> StaffMember:
    """Mark a pending invite as accepted. Called after the invitee's OTP/LINE login matches."""
    staff.accepted_at = utcnow()
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff


async def update_permissions(
    db: AsyncSession,
    staff: StaffMember,
    **flags: bool,
) -> StaffMember:
    for key, value in flags.items():
        if key not in VALID_PERMISSIONS:
            raise ValueError(f"Invalid permission: {key}")
        setattr(staff, key, bool(value))
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff


async def revoke_staff(db: AsyncSession, staff: StaffMember) -> StaffMember:
    staff.revoked_at = utcnow()
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff


async def list_staff(
    db: AsyncSession,
    shop_id: UUID,
    include_revoked: bool = False,
) -> List[StaffMember]:
    query = select(StaffMember).where(StaffMember.shop_id == shop_id)
    if not include_revoked:
        query = query.where(StaffMember.revoked_at.is_(None))
    query = query.order_by(StaffMember.invited_at)
    result = await db.exec(query)
    return list(result.all())


async def find_pending_invite_by_phone(
    db: AsyncSession, phone: str
) -> Optional[StaffMember]:
    """Used by the auth flow: when someone logs in via phone, check if they have a pending invite."""
    result = await db.exec(
        select(StaffMember)
        .where(
            StaffMember.phone == phone,
            StaffMember.accepted_at.is_(None),
            StaffMember.revoked_at.is_(None),
        )
        .order_by(StaffMember.invited_at.desc())
    )
    return result.first()


async def find_staff_by_line(
    db: AsyncSession, line_id: str
) -> Optional[StaffMember]:
    """Look up any (non-revoked) StaffMember by their LINE id — owner or
    invited. Used by the LINE callback to decide whether the visitor is
    an existing shop user vs. a fresh signup.
    """
    if not line_id:
        return None
    result = await db.exec(
        select(StaffMember)
        .where(
            StaffMember.line_id == line_id,
            StaffMember.revoked_at.is_(None),
        )
        .order_by(StaffMember.invited_at.desc())
    )
    return result.first()


async def find_staff_by_phone(
    db: AsyncSession, phone: str
) -> Optional[StaffMember]:
    """Same as find_staff_by_line but keyed on the phone number used for
    OTP login. Either an owner (is_owner=True, accepted_at set) or a
    pending/accepted invite.
    """
    if not phone:
        return None
    result = await db.exec(
        select(StaffMember)
        .where(
            StaffMember.phone == phone,
            StaffMember.revoked_at.is_(None),
        )
        .order_by(StaffMember.invited_at.desc())
    )
    return result.first()


async def create_owner_staff(
    db: AsyncSession,
    shop: Shop,
    *,
    line_id: Optional[str] = None,
    phone: Optional[str] = None,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> StaffMember:
    """Create the owner StaffMember row for a Shop. Owners have every
    permission set, accepted_at is stamped immediately (no invite-token
    dance), and is_owner=True so permission gates short-circuit.
    """
    staff = StaffMember(
        shop_id=shop.id,
        line_id=line_id,
        phone=phone,
        display_name=display_name,
        picture_url=picture_url,
        is_owner=True,
        accepted_at=utcnow(),
        can_void=True,
        can_deereach=True,
        can_topup=True,
        can_settings=True,
    )
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff


async def ensure_owner_staff(db: AsyncSession, shop: Shop) -> StaffMember:
    """Idempotent get-or-create for the owner StaffMember row.

    The auth callbacks lazy-create the owner row on first post-deploy
    sign-in, but customers/owners holding a legacy JWT that pre-dates
    the staff_id session field don't trigger that path until they
    re-login. ShopContextMiddleware calls this on every shop request so
    the row exists for *any* legacy session — once it's there, the
    middleware's normal SELECT finds it and skips the create.

    Backfills line_id and phone from the Shop row so subsequent logins
    via either provider match the existing owner-staff via
    find_staff_by_*.
    """
    result = await db.exec(
        select(StaffMember).where(
            StaffMember.shop_id == shop.id,
            StaffMember.is_owner == True,  # noqa: E712
            StaffMember.revoked_at.is_(None),
        )
    )
    existing = result.first()
    if existing is not None:
        return existing
    return await create_owner_staff(
        db,
        shop,
        line_id=shop.line_id,
        phone=shop.phone,
        display_name=shop.name,
    )
