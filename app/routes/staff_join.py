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
    find_pending_by_token,
    find_staff_by_username,
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


async def _resolve_shop_from_host(request: Request, db: AsyncSession) -> Optional[Shop]:
    """PIN login is keyed per-shop. On the shop subdomain we'd resolve
    via host; on the main domain owners can pass ?shop=<id> for QR
    flows. Returns None if neither pins down a shop."""
    host = request.headers.get("host", "").split(":")[0]
    if host.startswith("shop."):
        # shop.taemdee.com → look up via single-shop config (early dev)
        # or by future per-shop subdomain. For now we only support
        # main-domain ?shop= flow until per-shop subdomains land.
        pass
    shop_id = request.query_params.get("shop")
    if shop_id:
        from uuid import UUID
        try:
            return await db.get(Shop, UUID(shop_id))
        except (ValueError, TypeError):
            return None
    return None


@router.get("/staff/pin-login", response_class=HTMLResponse)
async def staff_pin_login_page(
    request: Request,
    shop: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
):
    """Username + 6-digit PIN sign-in. Owner sets credentials at staff
    creation; staff opens this page (typically via a QR the owner
    posts at the shop) and enters them. Shop is resolved via ?shop=
    when on the main domain — future per-shop subdomains can resolve
    via host."""
    resolved_shop = await _resolve_shop_from_host(request, db)
    return templates.TemplateResponse(
        request=request,
        name="staff_pin_login.html",
        context={"shop": resolved_shop},
    )


@router.post("/staff/pin-login")
async def staff_pin_login_post(
    request: Request,
    response: Response,
    username: str = Form(...),
    pin: str = Form(...),
    shop_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_session),
):
    """Validate username + PIN, issue a shop session JWT, redirect to
    the dashboard. Generic auth error on any miss (no enumeration of
    valid usernames)."""
    from uuid import UUID

    resolved_shop: Optional[Shop] = None
    if shop_id:
        try:
            resolved_shop = await db.get(Shop, UUID(shop_id))
        except (ValueError, TypeError):
            resolved_shop = None
    if resolved_shop is None:
        resolved_shop = await _resolve_shop_from_host(request, db)
    if resolved_shop is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "ไม่พบร้าน · เปิดจากลิงก์/QR ของร้านอีกครั้ง",
        )

    staff = await find_staff_by_username(db, resolved_shop.id, (username or "").strip())
    if staff is None or not verify_pin(pin, staff.pin_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Username หรือ PIN ไม่ถูกต้อง",
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
            resolved_shop.id, staff_id=staff.id, is_owner=staff.is_owner,
        ),
    )
    return redirect
