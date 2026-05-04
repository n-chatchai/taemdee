"""FastAPI dependencies for authentication & authorization.

Session is an httpOnly cookie carrying a JWT. Every protected route depends on
`get_current_shop` or `get_session_context`; owner-only / permission-gated routes
use `require_owner` or `require_permission(...)`.
"""

from dataclasses import dataclass
from typing import Callable, Optional
from uuid import UUID

from fastapi import Cookie, Depends, HTTPException, Response, status
from sqlmodel.ext.asyncio.session import AsyncSession
from loguru import logger

from app.core.database import get_session
from app.models import Customer, Shop, StaffMember
from app.services.auth import (
    decode_customer_token,
    decode_session_token,
    issue_customer_token,
)

SESSION_COOKIE_NAME = "session"
CUSTOMER_COOKIE_NAME = "customer"
CUSTOMER_COOKIE_MAX_AGE = 365 * 24 * 3600  # 1 year


class SessionAuthError(HTTPException):
    """401 raised when the shop session is missing / invalid / orphaned."""

    REASONS = {
        "session_missing": "ยังไม่ได้เข้าสู่ระบบ — กรุณาเข้าสู่ระบบเพื่อใช้แดชบอร์ด",
        "session_invalid": "Session ไม่ถูกต้องหรือหมดอายุ — กรุณาเข้าสู่ระบบใหม่",
        "session_shop_missing": "ไม่พบร้านค้าตามที่ session อ้างถึง (อาจถูกลบไปแล้ว) — กรุณาเข้าสู่ระบบใหม่",
        "session_staff_revoked": "พนักงานคนนี้ถูกถอนสิทธิ์การเข้าใช้งานแล้ว — ติดต่อเจ้าของร้าน",
    }

    def __init__(self, reason: str):
        if reason not in self.REASONS:
            raise ValueError(f"Unknown SessionAuthError reason: {reason}")
        self.reason = reason
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=self.REASONS[reason],
        )


class CustomerAuthError(HTTPException):
    """401 raised when a customer identity token is invalid or required."""

    REASONS = {
        "token_invalid": "รหัสสมาชิกไม่ถูกต้องหรือหมดอายุ — กรุณาเข้าสู่ระบบใหม่",
        "login_required": "กรุณาเข้าสู่ระบบเพื่อเข้าถึงส่วนนี้",
    }

    def __init__(self, reason: str):
        if reason not in self.REASONS:
            raise ValueError(f"Unknown CustomerAuthError reason: {reason}")
        self.reason = reason
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=self.REASONS[reason],
        )


@dataclass
class SessionContext:
    shop_id: UUID
    staff_id: Optional[UUID]
    role: str  # "owner" or "staff" (kept for legacy payloads / templates)
    _is_owner: bool = False

    @property
    def is_owner(self) -> bool:
        # New JWTs carry is_owner explicitly (owner is now a StaffMember
        # row with is_owner=True). Legacy JWTs that pre-date the
        # unification fall back to the role field.
        return self._is_owner or self.role == "owner"


async def get_session_context(
    session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
) -> SessionContext:
    if not session_cookie:
        raise SessionAuthError("session_missing")

    payload = decode_session_token(session_cookie)
    if not payload:
        raise SessionAuthError("session_invalid")

    try:
        return SessionContext(
            shop_id=UUID(payload["shop_id"]),
            staff_id=UUID(payload["staff_id"]) if payload.get("staff_id") else None,
            role=payload.get("role", "owner"),
            _is_owner=bool(payload.get("is_owner", False)),
        )
    except (KeyError, ValueError, TypeError):
        raise SessionAuthError("session_invalid")


async def get_current_shop(
    ctx: SessionContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_session),
) -> Shop:
    shop = await db.get(Shop, ctx.shop_id)
    if not shop:
        raise SessionAuthError("session_shop_missing")
    return shop


async def get_current_staff(
    ctx: SessionContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_session),
) -> Optional[StaffMember]:
    """Returns the StaffMember if the session is for a staff user, else None (owner)."""
    if ctx.staff_id is None:
        return None
    staff = await db.get(StaffMember, ctx.staff_id)
    if not staff or staff.revoked_at is not None:
        raise SessionAuthError("session_staff_revoked")
    return staff


def require_owner(
    ctx: SessionContext = Depends(get_session_context),
) -> SessionContext:
    if not ctx.is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner access required")
    return ctx


async def find_or_create_customer(
    customer_cookie: Optional[str],
    db: AsyncSession,
) -> tuple[Customer, bool]:
    """Resolve the customer for this request.
    - If valid cookie: return existing customer.
    - If invalid cookie: raise CustomerAuthError (triggers login redirect).
    - If no cookie: create new anonymous customer.
    """

    if customer_cookie:
        customer_id = decode_customer_token(customer_cookie)
        if not customer_id:
            raise CustomerAuthError("token_invalid")

        existing = await db.get(Customer, customer_id)
        if existing:
            return existing, False
        else:
            # Token valid but customer deleted? Also invalid.
            raise CustomerAuthError("token_invalid")

    new_customer = Customer(is_anonymous=True)
    db.add(new_customer)
    await db.commit()
    await db.refresh(new_customer)
    return new_customer, True


def set_customer_cookie(response: Response, customer_id: UUID) -> None:
    response.set_cookie(
        key=CUSTOMER_COOKIE_NAME,
        value=issue_customer_token(customer_id),
        httponly=True,
        secure=True,  # always Secure — local dev uses HTTPS (mkcert), prod uses HTTPS
        samesite="lax",
        max_age=CUSTOMER_COOKIE_MAX_AGE,
        path="/",
    )


def require_permission(perm: str) -> Callable:
    """Route dep: ensures the current user has the named StaffMember permission.

    Owners always pass. Staff must have the flag set (e.g., can_void, can_deereach).
    Usage: `Depends(require_permission("can_deereach"))`.
    """

    async def _check(
        ctx: SessionContext = Depends(get_session_context),
        staff: Optional[StaffMember] = Depends(get_current_staff),
    ) -> SessionContext:
        if ctx.is_owner:
            return ctx
        if staff is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Permission denied")
        if not getattr(staff, perm, False):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"Missing permission: {perm}"
            )
        return ctx

    return _check
