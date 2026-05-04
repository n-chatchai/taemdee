"""Team (staff) management — invite, accept, update permissions, revoke.

Provider link/disconnect for staff goes through services/identity.py
(same generic helpers the customer side uses); this module wraps them
with staff-specific glue (display_name + picture_url copy on link,
revoked_at scoping on lookups).
"""

import secrets
from datetime import timedelta
from typing import List, Optional
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Shop, StaffMember
from app.models.util import utcnow
from app.services.identity import (
    IdentityConflict,
    bind_provider,
    find_row_by_provider,
    unbind_provider,
)

VALID_PERMISSIONS = {"can_void", "can_deereach", "can_topup", "can_settings"}
INVITE_TOKEN_TTL_HOURS = 24

# Staff identity field set — no recovery_code (staff don't use the
# anonymous-claim flow), so the four social/phone columns are it.
_STAFF_IDENTITY_FIELDS = ("line_id", "google_id", "facebook_id", "phone")
_STAFF_LINK_CONFLICT = "บัญชีนี้ผูกกับสตาฟอื่นอยู่แล้ว"
_STAFF_LAST_IDENTITY = (
    "ปลดเชื่อมไม่ได้ · ต้องเหลืออย่างน้อย 1 ช่องทางเพื่อเข้าสู่ระบบครั้งหน้า"
)
# Common scoping for staff lookups — never resolve a revoked staff row.
_STAFF_LOOKUP_FILTERS = (StaffMember.revoked_at.is_(None),)


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


async def find_staff_by_provider(
    db: AsyncSession, provider: str, ext_id: str,
) -> Optional[StaffMember]:
    """Look up any (non-revoked) StaffMember by their bound provider id.
    Used by the OAuth + OTP callbacks to decide whether the visitor is
    an existing shop user (owner OR pending invite) vs. a fresh signup.
    Provider names match services/identity.PROVIDER_FIELDS.
    """
    return await find_row_by_provider(
        db, StaffMember, provider, ext_id,
        extra_filters=_STAFF_LOOKUP_FILTERS,
    )


# Back-compat aliases for the existing callsites in routes/auth.py +
# routes/shops.py. Same behaviour, fewer call sites going forward.
async def find_staff_by_line(db: AsyncSession, line_id: str) -> Optional[StaffMember]:
    return await find_staff_by_provider(db, "line", line_id)


async def find_staff_by_phone(db: AsyncSession, phone: str) -> Optional[StaffMember]:
    return await find_staff_by_provider(db, "phone", phone)


async def link_staff_provider(
    db: AsyncSession,
    staff: StaffMember,
    provider: str,
    ext_id: str,
    *,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> StaffMember:
    """Add a 2nd provider to an already-accepted staff row. Mirror of
    soft_wall.link_to_claimed for the customer side. Refuses to merge
    onto a different staff — surfaces as IdentityConflict.
    """
    staff = await bind_provider(
        db, staff, provider, ext_id,
        model=StaffMember,
        conflict_message=_STAFF_LINK_CONFLICT,
        extra_filters=_STAFF_LOOKUP_FILTERS,
    )
    if display_name and not staff.display_name:
        staff.display_name = display_name
    if picture_url and not staff.picture_url:
        staff.picture_url = picture_url
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff


async def disconnect_staff_provider(
    db: AsyncSession,
    staff: StaffMember,
    provider: str,
) -> StaffMember:
    """Unlink one provider from a staff row. Last-identity guard ensures
    the staff retains at least one way to sign in.
    """
    return await unbind_provider(
        db, staff, provider,
        identity_fields=_STAFF_IDENTITY_FIELDS,
        last_identity_message=_STAFF_LAST_IDENTITY,
    )


async def resolve_shop_signin(
    db: AsyncSession,
    provider: str,
    ext_id: str,
    *,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> tuple[Shop, StaffMember]:
    """Generic OAuth/OTP shop-side resolver — shared by LINE, Google,
    Facebook, and phone callbacks. Returns the (Shop, StaffMember) pair
    the route should issue a session for.

    Order:
      1. Find StaffMember by provider id. If pending, accept_invite +
         backfill missing identity / display_name / picture_url.
      2. No staff match → look up Shop by the matching column for
         line/phone (pre-unification shops still on the Shop row).
         Lazy-create the owner-staff. Google/Facebook: no Shop column,
         so step 2 is skipped.
      3. Neither → brand-new shop signup. Create Shop + owner-staff.
    """
    staff = await find_staff_by_provider(db, provider, ext_id)
    if staff is not None:
        if staff.accepted_at is None:
            await accept_invite(db, staff)
            # Backfill the OAuth identity column + profile bits the
            # invite row was missing. The matching `provider` column
            # was already set by the owner during the invite (or it
            # would not have matched in find_staff_by_provider).
            changed = False
            if display_name and not staff.display_name:
                staff.display_name = display_name
                changed = True
            if picture_url and not staff.picture_url:
                staff.picture_url = picture_url
                changed = True
            if changed:
                db.add(staff)
                await db.commit()
                await db.refresh(staff)
        shop = await db.get(Shop, staff.shop_id)
        return shop, staff

    # Step 2 — pre-unification shops have phone/line_id on Shop directly.
    # google_id / facebook_id are staff-only columns, so the lookup
    # naturally falls through to step 3 for those providers.
    shop: Optional[Shop] = None
    if provider == "line":
        shop = (await db.exec(select(Shop).where(Shop.line_id == ext_id))).first()
    elif provider == "phone":
        shop = (await db.exec(select(Shop).where(Shop.phone == ext_id))).first()

    if shop is None:
        # Step 3 — fresh signup.
        if provider == "line":
            shop = Shop(line_id=ext_id, name=display_name or "Shop")
        elif provider == "phone":
            shop = Shop(phone=ext_id, name=display_name or "Shop")
        else:
            shop = Shop(name=display_name or "Shop")
        db.add(shop)
        await db.commit()
        await db.refresh(shop)

    staff = await create_owner_staff(
        db, shop,
        line_id=ext_id if provider == "line" else None,
        google_id=ext_id if provider == "google" else None,
        facebook_id=ext_id if provider == "facebook" else None,
        phone=ext_id if provider == "phone" else None,
        display_name=display_name,
        picture_url=picture_url,
    )
    return shop, staff


async def create_owner_staff(
    db: AsyncSession,
    shop: Shop,
    *,
    line_id: Optional[str] = None,
    phone: Optional[str] = None,
    google_id: Optional[str] = None,
    facebook_id: Optional[str] = None,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> StaffMember:
    """Create the owner StaffMember row for a Shop. Owners have every
    permission set, accepted_at is stamped immediately (no invite-token
    dance), and is_owner=True so permission gates short-circuit.
    Accepts any of the four provider identity columns so all four
    OAuth/OTP shop callbacks can use a common create path.
    """
    staff = StaffMember(
        shop_id=shop.id,
        line_id=line_id,
        google_id=google_id,
        facebook_id=facebook_id,
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
