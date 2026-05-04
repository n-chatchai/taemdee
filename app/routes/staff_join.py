"""Staff.join — public landing page reached from the invite QR/link.

Auth-free (the visitor is a new staff member, no session yet). Renders
the shop name + LINE/phone login buttons. Login flows hand off to the
existing /auth/line and /auth/otp endpoints; the staff record is matched
on the post-login callback (TODO: wire token into the callback so we can
flip accepted_at + bind line_id/phone).

Also hosts the username/PIN login at /staff/pin-login. Shop owners
set username + 6-digit PIN at staff creation; staff signs in here
with those credentials. No OAuth/SMS round-trip needed."""

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from app.core.templates import templates
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import SESSION_COOKIE_NAME
from app.core.config import settings
from app.core.database import get_session
from app.models import Shop
from app.services.auth import issue_session_token
from app.services.team import (
    accept_invite,
    find_active_staff_for_user,
    find_pending_by_token,
    find_user_by_username,
    is_valid_pin,
    register_shop_with_pin,
    verify_pin,
)

router = APIRouter()


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=30 * 24 * 3600,
        path="/",
    )


def _bounce_to_shop_host_if_needed(request: Request) -> Optional[RedirectResponse]:
    """If we're on the main domain, redirect to the shop subdomain so
    the session cookie lands on the right host. /shop/dashboard is
    served only on the shop subdomain — a cookie set on main would be
    invisible to the dashboard request after the SubdomainRouting
    middleware bounces it across hosts."""
    host = request.headers.get("host", "").split(":")[0]
    is_shop_host = host.startswith("shop.") or host == settings.shop_domain
    if is_shop_host:
        return None
    shop_host = (
        settings.shop_domain
        if settings.environment == "production"
        else f"shop.{host}"
    )
    proto = request.url.scheme
    return RedirectResponse(
        url=f"{proto}://{shop_host}{request.url.path}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/staff/join", response_class=HTMLResponse)
async def staff_join_page(
    request: Request,
    t: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
):
    """Landing page for the staff invite. Shows shop name + nickname so
    the staff can confirm "this is me, I want to join", then offers
    LINE / phone login. Bad/expired token → friendly invite-expired
    state (still 200) so the staff knows to ask the owner for a fresh QR."""
    staff = await find_pending_by_token(db, t or "")
    shop = await db.get(Shop, staff.shop_id) if staff else None
    return templates.TemplateResponse(
        request=request,
        name="staff_join.html",
        context={
            "staff": staff,
            "shop": shop,
            "token": t or "",
        },
    )


@router.get("/staff/pin-login", response_class=HTMLResponse)
async def staff_pin_login_page(request: Request):
    """Username + 6-digit PIN sign-in. Username is globally unique on
    User; the login resolves to whichever active staff record the
    user has (accepted-first, earliest invite). Shop-side only —
    customer surfaces are connect-only and don't expose this UI."""
    bounce = _bounce_to_shop_host_if_needed(request)
    if bounce is not None:
        return bounce
    return templates.TemplateResponse(
        request=request,
        name="staff_pin_login.html",
        context={},
    )


def _render_pin_form(
    request: Request,
    error: Optional[str] = None,
    username_value: str = "",
    shop_name_value: str = "",
    initial_step: str = "login",
):
    """Re-render the PIN login form with an inline error. We don't
    raise 401/403 directly because the global auth-error handler
    catches plain 401s and redirects to /shop/login?reason=invalid —
    confusingly making a wrong-PIN look like a session error. Inline
    error keeps the user on the form."""
    return templates.TemplateResponse(
        request=request,
        name="staff_pin_login.html",
        context={
            "error": error,
            "username_value": username_value,
            "shop_name_value": shop_name_value,
            "initial_step": initial_step,
        },
        status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
    )


@router.post("/staff/pin-login")
async def staff_pin_login_post(
    request: Request,
    response: Response,
    username: str = Form(...),
    pin: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    """Validate username + PIN, issue a shop session JWT, redirect to
    the dashboard. Wrong username/PIN re-renders the form with an
    inline error (not 401 — that gets caught by the global auth
    handler and redirected to /shop/login?reason=invalid which
    misleads the user)."""
    host = request.headers.get("host", "").split(":")[0]
    is_shop_host = host.startswith("shop.") or host == settings.shop_domain
    if not is_shop_host:
        # POST on main means the GET-time bounce didn't run. Re-bounce
        # using the form's submitted credentials would leak them via
        # query string, so just render an instructional error.
        return _render_pin_form(
            request,
            error="เปิดหน้านี้บนโดเมนร้านค้าเท่านั้น",
            username_value=username or "",
        )

    user = await find_user_by_username(db, (username or "").strip())
    if user is None or not verify_pin(pin, user.pin_hash):
        return _render_pin_form(
            request,
            error="Username หรือ PIN ไม่ถูกต้อง",
            username_value=username or "",
        )

    staff = await find_active_staff_for_user(db, user.id)
    if staff is None:
        return _render_pin_form(
            request,
            error="บัญชีนี้ยังไม่ได้ผูกกับร้าน · ติดต่อเจ้าของร้านเพื่อรับ invite",
            username_value=username or "",
        )

    # First successful login flips accepted_at if it was a pending
    # invite — same semantics as OAuth-via-invite.
    if staff.accepted_at is None:
        await accept_invite(db, staff)

    redirect = RedirectResponse(
        url="/shop/dashboard", status_code=status.HTTP_303_SEE_OTHER,
    )
    _set_session_cookie(
        redirect,
        issue_session_token(
            staff.shop_id, staff_id=staff.id, is_owner=staff.is_owner,
        ),
    )
    return redirect


@router.post("/staff/pin-register")
async def staff_pin_register_post(
    request: Request,
    response: Response,
    username: str = Form(...),
    pin: str = Form(...),
    shop_name: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    """Brand-new owner sign-up via username + PIN. Creates a fresh
    User + Shop + owner-staff and issues the session JWT. Independent
    of OAuth and invites — this is how someone with no LINE/Google/
    phone bootstraps a shop."""
    host = request.headers.get("host", "").split(":")[0]
    is_shop_host = host.startswith("shop.") or host == settings.shop_domain
    if not is_shop_host:
        return _render_pin_form(
            request,
            error="เปิดหน้านี้บนโดเมนร้านค้าเท่านั้น",
            username_value=username or "",
            shop_name_value=shop_name or "",
            initial_step="register",
        )

    uname = (username or "").strip()
    pin_value = (pin or "").strip()
    name = (shop_name or "").strip()

    if not uname:
        return _render_pin_form(
            request, error="ใส่ Username", initial_step="register",
            shop_name_value=name,
        )
    if not is_valid_pin(pin_value):
        return _render_pin_form(
            request, error="PIN ต้องเป็นตัวเลข 6 หลัก",
            username_value=uname, shop_name_value=name, initial_step="register",
        )
    if not name:
        return _render_pin_form(
            request, error="ใส่ชื่อร้าน",
            username_value=uname, initial_step="register",
        )

    existing = await find_user_by_username(db, uname)
    if existing is not None:
        return _render_pin_form(
            request,
            error=f"Username '{uname}' มีคนใช้แล้ว · ลองชื่ออื่น",
            username_value=uname, shop_name_value=name, initial_step="register",
        )

    shop, staff = await register_shop_with_pin(
        db, username=uname, pin=pin_value, shop_name=name,
    )

    redirect = RedirectResponse(
        url="/shop/dashboard", status_code=status.HTTP_303_SEE_OTHER,
    )
    _set_session_cookie(
        redirect,
        issue_session_token(
            staff.shop_id, staff_id=staff.id, is_owner=staff.is_owner,
        ),
    )
    return redirect
