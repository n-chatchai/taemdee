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


@router.post("/staff/pin-login")
async def staff_pin_login_post(
    request: Request,
    response: Response,
    username: str = Form(...),
    pin: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    """Validate username + PIN, issue a shop session JWT, redirect to
    the dashboard. Generic auth error on any miss (no enumeration of
    valid usernames). Refuses if the User has no active staff record
    — credentials only authenticate a person, they don't grant shop
    access on their own.

    Refuses POST on the main host: the form is rendered after the
    GET handler bounces to shop subdomain, so any POST that lands on
    main is either a misrouted client or a curl test. Honoring it
    would set the session cookie on the wrong host."""
    host = request.headers.get("host", "").split(":")[0]
    is_shop_host = host.startswith("shop.") or host == settings.shop_domain
    if not is_shop_host:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "เปิดหน้านี้บนโดเมนร้านค้าเท่านั้น",
        )

    user = await find_user_by_username(db, (username or "").strip())
    if user is None or not verify_pin(pin, user.pin_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Username หรือ PIN ไม่ถูกต้อง",
        )

    staff = await find_active_staff_for_user(db, user.id)
    if staff is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "บัญชีนี้ยังไม่ได้ผูกกับร้าน · ติดต่อเจ้าของร้านเพื่อรับ invite",
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
