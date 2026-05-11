"""Admin surface — single super-user dashboard at admin.taemdee.com.

Auth is intentionally separate from customer / shop OAuth:
  · `settings.admin_pin` (env var) is the only credential. Empty PIN
    disables the surface entirely (login returns 503).
  · A signed `admin_session` cookie carries the timestamp of last
    successful login; it expires after `ADMIN_SESSION_TTL`.

Routes:
  · GET  /admin/login         — PIN entry form
  · POST /admin/login         — verify, set cookie, redirect dashboard
  · POST /admin/logout        — clear cookie + bounce to /admin/login
  · GET  /admin/dashboard     — overview counts (shops, customers, points,
                                broadcasts last 7d, credits in circulation)
  · GET  /admin/shops         — paginated shop list + impersonate link
  · GET  /admin/customers     — paginated customer list
  · GET  /admin/topups        — TopupSlip list (approve/reject)
  · GET  /admin/deereach      — DeeReachCampaign list w/ stats
  · POST /admin/impersonate/shop/<id> — mint a shop session for the
                                         operator → bounce to /shop/dashboard
"""
from __future__ import annotations

import hmac
import hashlib
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.templates import templates
from app.models import (
    Customer,
    DeeReachCampaign,
    Inbox,
    Point,
    Redemption,
    Shop,
    TopupSlip,
    User,
)

router = APIRouter()


ADMIN_COOKIE = "admin_session"
ADMIN_SESSION_TTL = timedelta(hours=12)


def _sign(payload: str) -> str:
    """HMAC-SHA256 the payload with settings.jwt_secret. The cookie's
    `value.sig` shape lets us round-trip the issued-at timestamp
    without a server-side store — useful while admin surface is one
    super-user and we don't need session revocation."""
    return hmac.new(
        settings.jwt_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _issue_cookie() -> str:
    """`<issued_at_epoch>.<hex_sig>` — verified by _verify_cookie."""
    issued = str(int(time.time()))
    return f"{issued}.{_sign(issued)}"


def _verify_cookie(raw: Optional[str]) -> bool:
    if not raw or "." not in raw:
        return False
    issued_str, sig = raw.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(issued_str)):
        return False
    try:
        issued = int(issued_str)
    except ValueError:
        return False
    age = time.time() - issued
    return 0 <= age <= ADMIN_SESSION_TTL.total_seconds()


def _require_admin(admin_session: Optional[str]) -> None:
    """Raise 401 if the cookie is missing / forged / expired. Used as
    a guard inline at the top of every protected route."""
    if not _verify_cookie(admin_session):
        # 401 is caught by the global auth_error_handler in main.py
        # which redirects to /shop/login by default — admin routes
        # need their own 303 redirect to /admin/login instead.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin auth required",
        )


def _admin_redirect_to_login() -> RedirectResponse:
    return RedirectResponse(
        url="/admin/login", status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/login", response_class=HTMLResponse)
async def admin_login_form(
    request: Request,
    error: Optional[str] = None,
):
    if not settings.admin_pin:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Admin surface is disabled — set ADMIN_PIN env var to enable.",
        )
    return templates.TemplateResponse(
        request=request,
        name="admin/login.html",
        context={"error": error},
    )


@router.post("/login")
async def admin_login_post(
    pin: str = Form(""),
):
    if not settings.admin_pin:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE)
    if not hmac.compare_digest(pin.strip(), settings.admin_pin):
        return RedirectResponse(
            url="/admin/login?error=invalid",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    response = RedirectResponse(
        url="/admin/dashboard", status_code=status.HTTP_303_SEE_OTHER,
    )
    response.set_cookie(
        ADMIN_COOKIE,
        _issue_cookie(),
        max_age=int(ADMIN_SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
        path="/",
    )
    return response


@router.post("/logout")
async def admin_logout():
    response = RedirectResponse(
        url="/admin/login", status_code=status.HTTP_303_SEE_OTHER,
    )
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return response


@router.get("/", response_class=HTMLResponse)
async def admin_root():
    return RedirectResponse(
        url="/admin/dashboard", status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    admin_session: Optional[str] = Cookie(None, alias=ADMIN_COOKIE),
    db: AsyncSession = Depends(get_session),
):
    if not _verify_cookie(admin_session):
        return _admin_redirect_to_login()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    week_ago = now - timedelta(days=7)

    shop_count = int((await db.exec(select(func.count()).select_from(Shop))).one() or 0)
    customer_count = int((await db.exec(select(func.count()).select_from(Customer))).one() or 0)
    user_count = int((await db.exec(select(func.count()).select_from(User))).one() or 0)
    point_count = int((await db.exec(
        select(func.count()).select_from(Point).where(Point.is_voided == False)  # noqa: E712
    )).one() or 0)
    inbox_count = int((await db.exec(select(func.count()).select_from(Inbox))).one() or 0)
    broadcasts_7d = int((await db.exec(
        select(func.count()).select_from(DeeReachCampaign).where(
            DeeReachCampaign.sent_at.is_not(None),
            DeeReachCampaign.sent_at >= week_ago,
        )
    )).one() or 0)
    pending_topups = int((await db.exec(
        select(func.count()).select_from(TopupSlip).where(TopupSlip.status == "pending")
    )).one() or 0)

    return templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context={
            "shop_count": shop_count,
            "customer_count": customer_count,
            "user_count": user_count,
            "point_count": point_count,
            "inbox_count": inbox_count,
            "broadcasts_7d": broadcasts_7d,
            "pending_topups": pending_topups,
        },
    )


@router.get("/shops", response_class=HTMLResponse)
async def admin_shops(
    request: Request,
    admin_session: Optional[str] = Cookie(None, alias=ADMIN_COOKIE),
    db: AsyncSession = Depends(get_session),
):
    if not _verify_cookie(admin_session):
        return _admin_redirect_to_login()
    shops = (await db.exec(
        select(Shop).order_by(Shop.created_at.desc()).limit(200)
    )).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/shops.html",
        context={"shops": shops},
    )


@router.get("/shops/{shop_id}", response_class=HTMLResponse)
async def admin_shop_detail(
    request: Request,
    shop_id: UUID,
    admin_session: Optional[str] = Cookie(None, alias=ADMIN_COOKIE),
    db: AsyncSession = Depends(get_session),
):
    if not _verify_cookie(admin_session):
        return _admin_redirect_to_login()

    from app.models import StaffMember

    shop = await db.get(Shop, shop_id)
    if shop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "shop not found")

    staff_rows = (await db.exec(
        select(StaffMember).where(
            StaffMember.shop_id == shop_id,
            StaffMember.revoked_at.is_(None),
        ).order_by(StaffMember.is_owner.desc(), StaffMember.invited_at.asc())
    )).all()

    # Hydrate bound users in one query so the template can show
    # display_name + phone without lazy-loading on each row.
    user_ids = [s.user_id for s in staff_rows if s.user_id]
    users_by_id: dict = {}
    if user_ids:
        urows = (await db.exec(select(User).where(User.id.in_(user_ids)))).all()
        users_by_id = {u.id: u for u in urows}

    # Recent broadcasts (last 10) — gives the admin a snapshot of the
    # shop's outbound activity without opening /admin/deereach.
    campaigns = (await db.exec(
        select(DeeReachCampaign)
        .where(
            DeeReachCampaign.shop_id == shop_id,
            DeeReachCampaign.sent_at.is_not(None),
        )
        .order_by(DeeReachCampaign.sent_at.desc())
        .limit(10)
    )).all()

    point_total = int((await db.exec(
        select(func.count()).select_from(Point).where(
            Point.shop_id == shop_id,
            Point.is_voided == False,  # noqa: E712
        )
    )).one() or 0)
    customer_count = int((await db.exec(
        select(func.count(func.distinct(Point.customer_id)))
        .where(Point.shop_id == shop_id)
    )).one() or 0)

    return templates.TemplateResponse(
        request=request,
        name="admin/shop_detail.html",
        context={
            "shop": shop,
            "staff_rows": staff_rows,
            "users_by_id": users_by_id,
            "campaigns": campaigns,
            "point_total": point_total,
            "customer_count": customer_count,
        },
    )


@router.get("/customers", response_class=HTMLResponse)
async def admin_customers(
    request: Request,
    admin_session: Optional[str] = Cookie(None, alias=ADMIN_COOKIE),
    db: AsyncSession = Depends(get_session),
):
    if not _verify_cookie(admin_session):
        return _admin_redirect_to_login()
    rows = (await db.exec(
        select(Customer).order_by(Customer.created_at.desc()).limit(200)
    )).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/customers.html",
        context={"customers": rows},
    )


@router.get("/topups", response_class=HTMLResponse)
async def admin_topups(
    request: Request,
    admin_session: Optional[str] = Cookie(None, alias=ADMIN_COOKIE),
    db: AsyncSession = Depends(get_session),
):
    if not _verify_cookie(admin_session):
        return _admin_redirect_to_login()
    slips = (await db.exec(
        select(TopupSlip).order_by(TopupSlip.created_at.desc()).limit(200)
    )).all()
    shop_ids = {s.shop_id for s in slips}
    shops_by_id: dict = {}
    if shop_ids:
        shop_rows = (await db.exec(select(Shop).where(Shop.id.in_(shop_ids)))).all()
        shops_by_id = {s.id: s for s in shop_rows}
    return templates.TemplateResponse(
        request=request,
        name="admin/topups.html",
        context={"slips": slips, "shops_by_id": shops_by_id},
    )


@router.get("/deereach", response_class=HTMLResponse)
async def admin_deereach(
    request: Request,
    admin_session: Optional[str] = Cookie(None, alias=ADMIN_COOKIE),
    db: AsyncSession = Depends(get_session),
):
    if not _verify_cookie(admin_session):
        return _admin_redirect_to_login()
    campaigns = (await db.exec(
        select(DeeReachCampaign)
        .where(DeeReachCampaign.sent_at.is_not(None))
        .order_by(DeeReachCampaign.sent_at.desc())
        .limit(100)
    )).all()
    shop_ids = {c.shop_id for c in campaigns}
    shops_by_id: dict = {}
    if shop_ids:
        shop_rows = (await db.exec(select(Shop).where(Shop.id.in_(shop_ids)))).all()
        shops_by_id = {s.id: s for s in shop_rows}
    return templates.TemplateResponse(
        request=request,
        name="admin/deereach.html",
        context={"campaigns": campaigns, "shops_by_id": shops_by_id},
    )


@router.post("/impersonate/shop/{shop_id}")
async def admin_impersonate_shop(
    shop_id: UUID,
    admin_session: Optional[str] = Cookie(None, alias=ADMIN_COOKIE),
    db: AsyncSession = Depends(get_session),
):
    """Mint a staff session for the shop's owner and bounce to
    /shop/dashboard. Cross-origin redirect crosses to shop.taemdee.com
    where the cookie applies (staff session cookie is set on the shop
    subdomain). Owner-only staff so the admin gets max-privilege
    inside the shop."""
    if not _verify_cookie(admin_session):
        return _admin_redirect_to_login()

    from app.models import StaffMember
    from app.core.auth import SESSION_COOKIE_NAME
    from app.services.auth import issue_session_token

    shop = await db.get(Shop, shop_id)
    if shop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "shop not found")

    owner = (await db.exec(
        select(StaffMember).where(
            StaffMember.shop_id == shop_id,
            StaffMember.is_owner == True,  # noqa: E712
            StaffMember.user_id.is_not(None),
            StaffMember.revoked_at.is_(None),
        ).limit(1)
    )).first()
    if owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no owner staff found")

    token = issue_session_token(shop.id, staff_id=owner.id, is_owner=True)
    target_host = (
        settings.shop_domain
        if settings.environment == "production"
        else "shop.taemdee.local"
    )
    response = RedirectResponse(
        url=f"https://{target_host}/shop/dashboard",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=60 * 60 * 24 * settings.session_expire_days,
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
        domain=settings.domain_name if settings.environment == "production" else None,
        path="/",
    )
    return response
