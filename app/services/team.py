"""Team (staff) management — invite, accept, update permissions, revoke.

Provider link/disconnect for staff goes through services/identity.py
(same generic helpers the customer side uses); this module wraps them
with staff-specific glue (display_name + picture_url copy on link,
revoked_at scoping on lookups).

Identity (line_id / google_id / facebook_id / phone / display_name /
picture_url) lives on User. StaffMember holds role-specific bits +
user_id FK. When a user signs in via a provider that's already on a
*different* User, services/identity.bind_provider absorbs the other
user via merge_users — same person, no IdentityConflict.
"""

import secrets
from datetime import timedelta
from typing import List, Optional
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Shop, StaffMember, User
from app.models.util import utcnow
from app.services.identity import (
    PROVIDER_FIELDS,
    bind_provider,
    find_user_by_provider,
    unbind_provider,
)

VALID_PERMISSIONS = {"can_void", "can_deereach", "can_topup", "can_settings"}
INVITE_TOKEN_TTL_HOURS = 24

# bcrypt — already a dep via passlib[bcrypt]. Direct bcrypt API is
# fine here since we don't need the algo-mux features of passlib.
import bcrypt  # noqa: E402


def hash_pin(pin: str) -> str:
    """bcrypt-hash a 6-digit PIN. Caller validates the digit shape
    before calling — we don't enforce here so test fixtures can use
    short strings."""
    return bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_pin(pin: str, pin_hash: Optional[str]) -> bool:
    if not pin or not pin_hash:
        return False
    try:
        return bcrypt.checkpw(pin.encode("utf-8"), pin_hash.encode("utf-8"))
    except Exception:
        return False


def is_valid_pin(pin: str) -> bool:
    """6 digits, no spaces, no other chars."""
    return bool(pin) and len(pin) == 6 and pin.isdigit()


async def find_user_by_username(
    db: AsyncSession,
    username: str,
) -> Optional[User]:
    """Look up a User by username. Returns None for unknown / NULL.
    Caller's PIN check then short-circuits to a generic auth error.
    Username is globally unique on User (the credential authenticates
    a person, not a shop role)."""
    if not username:
        return None
    result = await db.exec(
        select(User).where(User.username == username)
    )
    return result.first()


async def find_active_staff_for_user(
    db: AsyncSession, user_id: UUID,
) -> Optional[StaffMember]:
    """Pick the staff record to sign the user into after a PIN login.
    Mirrors _find_active_staff_for_user used inside resolve_shop_signin
    (accepted-first ordering) — exposed for the PIN login route which
    has a User but needs to land on a Shop+Staff session."""
    return await _find_active_staff_for_user(db, user_id)

# Staff identity field set — no recovery_code (staff don't use the
# anonymous-claim flow), so the four social/phone columns are it.
_STAFF_IDENTITY_FIELDS = ("line_id", "google_id", "facebook_id", "phone")
_STAFF_LAST_IDENTITY = (
    "ปลดเชื่อมไม่ได้ · ต้องเหลืออย่างน้อย 1 ช่องทางเพื่อเข้าสู่ระบบครั้งหน้า"
)


def _generate_invite_token() -> str:
    """24 url-safe chars · ~144 bits entropy · single-use, 24h TTL."""
    return secrets.token_urlsafe(18)


async def _ensure_user_for_provider(
    db: AsyncSession,
    provider: str,
    ext_id: str,
    *,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> User:
    """Find-or-create the User row that owns this provider id. Backfills
    display_name + picture_url on the existing user when its slots are
    empty so freshly-arriving profile data isn't dropped.
    """
    field = PROVIDER_FIELDS[provider]
    user = await find_user_by_provider(db, User, provider, ext_id)
    if user is None:
        user = User(**{field: ext_id})
        if display_name:
            user.display_name = display_name
        if picture_url:
            user.picture_url = picture_url
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    changed = False
    if display_name and not user.display_name:
        user.display_name = display_name
        changed = True
    if picture_url and not user.picture_url:
        user.picture_url = picture_url
        changed = True
    if changed:
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


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
    """Create a pending StaffMember tied to a User row. The invitee
    accepts by logging in with the matching phone or LINE id (see
    `accept_invite`). When the provided phone/line_id already belongs
    to a User, we re-use that User so the invitee's existing identity
    auto-matches on first sign-in."""
    if not phone and not line_id:
        raise ValueError("Staff invite requires phone or line_id")

    provider = "phone" if phone else "line"
    ext_id = phone or line_id
    user = await _ensure_user_for_provider(
        db, provider, ext_id, display_name=display_name,
    )

    staff = StaffMember(
        shop_id=shop.id,
        user_id=user.id,
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
    """Used by the auth flow: when someone logs in via phone, check if
    they have a pending invite. Joins User on phone since phone now
    lives there."""
    result = await db.exec(
        select(StaffMember)
        .join(User, StaffMember.user_id == User.id)
        .where(
            User.phone == phone,
            StaffMember.accepted_at.is_(None),
            StaffMember.revoked_at.is_(None),
        )
        .order_by(StaffMember.invited_at.desc())
    )
    return result.first()


async def link_staff_provider(
    db: AsyncSession,
    staff: StaffMember,
    provider: str,
    ext_id: str,
    *,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
) -> StaffMember:
    """Add a 2nd provider to an already-accepted staff row. Calls
    bind_provider on the staff's User — when that ext_id already lives
    on a different User, that other User is absorbed into staff.user
    via merge_users (no IdentityConflict).
    """
    await bind_provider(db, staff.user, provider, ext_id)
    user = staff.user
    changed = False
    if display_name and not user.display_name:
        user.display_name = display_name
        changed = True
    if picture_url and not user.picture_url:
        user.picture_url = picture_url
        changed = True
    if changed:
        db.add(user)
        await db.commit()
    await db.refresh(staff)
    return staff


async def disconnect_staff_provider(
    db: AsyncSession,
    staff: StaffMember,
    provider: str,
) -> StaffMember:
    """Unlink one provider from a staff row's User. Last-identity guard
    ensures the staff retains at least one way to sign in.
    """
    await unbind_provider(
        db, staff.user, provider,
        identity_fields=_STAFF_IDENTITY_FIELDS,
        last_identity_message=_STAFF_LAST_IDENTITY,
    )
    await db.refresh(staff)
    return staff


async def _find_active_staff_for_user(
    db: AsyncSession, user_id: UUID,
) -> Optional[StaffMember]:
    """Pick a non-revoked StaffMember for this user. Accepted rows win
    over pending ones; among same-state rows the earliest invite wins
    so the choice is stable across calls. A user may staff multiple
    shops — the first active row is the one we sign in to.
    """
    result = await db.exec(
        select(StaffMember)
        .where(
            StaffMember.user_id == user_id,
            StaffMember.revoked_at.is_(None),
        )
        .order_by(
            StaffMember.accepted_at.is_(None),  # accepted (False) sorts first
            StaffMember.invited_at,
        )
    )
    return result.first()


async def resolve_shop_signin(
    db: AsyncSession,
    provider: str,
    ext_id: str,
    *,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
    existing_staff: Optional[StaffMember] = None,
) -> tuple[Shop, StaffMember]:
    """Generic OAuth/OTP shop-side resolver — shared by LINE, Google,
    Facebook, and phone callbacks. Returns the (Shop, StaffMember) pair
    the route should issue a session for.

    Order:
      0. If `existing_staff` is supplied, treat the round-trip as "add
         this provider to my current staff row" — bind_provider on
         existing_staff.user (auto-merges any other user holding this
         ext_id, no conflict). Used when the connect-row CTA on
         /shop/settings kicks off OAuth from a logged-in session.
      1. Find User by provider id, then resolve to their non-revoked
         StaffMember. If pending, accept_invite + backfill missing
         display_name / picture_url on the user.
      2. No staff match → look up Shop by line_id / phone (pre-
         unification shops still on the Shop row). Lazy-create the
         owner-staff bound to the (find-or-created) user.
      3. Neither → brand-new shop signup. Create Shop + owner-staff +
         user.
    """
    # Step 0 — linking path. Bind the provider to the active staff's
    # user; bind_provider absorbs any other user already holding this
    # ext_id, so there's no separate conflict branch any more.
    if existing_staff is not None:
        staff = await link_staff_provider(
            db, existing_staff, provider, ext_id,
            display_name=display_name, picture_url=picture_url,
        )
        shop = await db.get(Shop, staff.shop_id)
        return shop, staff

    matched_user = await find_user_by_provider(db, User, provider, ext_id)

    # Step 1 — user found. Resolve to an active staff row.
    if matched_user is not None:
        staff = await _find_active_staff_for_user(db, matched_user.id)
        if staff is not None:
            if staff.accepted_at is None:
                await accept_invite(db, staff)
                # Backfill profile bits the user row was missing.
                changed = False
                if display_name and not matched_user.display_name:
                    matched_user.display_name = display_name
                    changed = True
                if picture_url and not matched_user.picture_url:
                    matched_user.picture_url = picture_url
                    changed = True
                if changed:
                    db.add(matched_user)
                    await db.commit()
                    await db.refresh(matched_user)
                await db.refresh(staff)
            shop = await db.get(Shop, staff.shop_id)
            return shop, staff

    # Step 2 — pre-unification shops have phone/line_id on Shop directly.
    # google_id / facebook_id are not on Shop, so the lookup naturally
    # falls through to step 3 for those providers.
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

    user = matched_user or await _ensure_user_for_provider(
        db, provider, ext_id,
        display_name=display_name, picture_url=picture_url,
    )
    staff = await create_owner_staff(db, shop, user=user)
    return shop, staff


async def create_owner_staff(
    db: AsyncSession,
    shop: Shop,
    *,
    user: User,
) -> StaffMember:
    """Create the owner StaffMember row for a Shop. Owners have every
    permission set, accepted_at is stamped immediately (no invite-token
    dance), and is_owner=True so permission gates short-circuit. The
    User row carries the identity columns; we just bind the role.
    """
    staff = StaffMember(
        shop_id=shop.id,
        user_id=user.id,
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

    Backfills the User row from Shop.line_id / Shop.phone so subsequent
    logins via either provider match the existing owner-staff via
    find_user_by_provider.
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

    # Find-or-create a User from whatever Shop carries. If the shop
    # has neither line_id nor phone (Google/Facebook signup), create
    # an empty user — the auth callback that triggered this lazy path
    # will bind the ext_id afterwards.
    user: Optional[User] = None
    if shop.line_id:
        user = await _ensure_user_for_provider(
            db, "line", shop.line_id, display_name=shop.name,
        )
    elif shop.phone:
        user = await _ensure_user_for_provider(
            db, "phone", shop.phone, display_name=shop.name,
        )
    if user is None:
        user = User(display_name=shop.name)
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return await create_owner_staff(db, shop, user=user)
