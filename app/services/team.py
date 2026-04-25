"""Team (staff) management — invite, accept, update permissions, revoke."""

from typing import List, Optional
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Shop, StaffMember
from app.models.util import utcnow

VALID_PERMISSIONS = {"can_void", "can_deereach", "can_topup", "can_settings"}


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
