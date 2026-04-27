from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import (
    CUSTOMER_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    find_or_create_customer,
    set_customer_cookie,
)
from app.core.config import settings
from app.core.database import get_session
from app.models import Customer, Shop
from app.services.auth import generate_and_send_otp, issue_session_token, verify_otp
from app.services.soft_wall import claim_by_line
from app.services.referrals import consume_referral_on_signup, find_referral_by_code
from app.services.line_login import (
    LineLoginError,
    build_authorize_url,
    exchange_code_for_token,
    fetch_profile,
    is_configured as line_is_configured,
    make_oauth_state,
    verify_oauth_state,
)

router = APIRouter()

LINE_STATE_COOKIE = "line_oauth_state"


@router.post("/otp/request")
async def request_otp(
    phone: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    code = await generate_and_send_otp(db, phone)
    res = {"ok": True}
    if settings.login_otp_simulate:
        res["code"] = code
    return res


@router.post("/otp/verify")
async def verify_and_login(
    response: Response,
    phone: str = Form(...),
    code: str = Form(...),
    name: str = Form("New Shop"),
    ref: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_session),
):
    # In simulate mode the server already gave the client the real code,
    # so we trust the submission directly — no DB round-trip needed.
    if not settings.login_otp_simulate:
        if not await verify_otp(db, phone, code):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired code")

    result = await db.exec(select(Shop).where(Shop.phone == phone))
    shop = result.first()
    is_new = shop is None

    if is_new:
        shop = Shop(name=name, phone=phone)
        db.add(shop)
        await db.commit()
        await db.refresh(shop)

        # Bind referral on first signup if a valid open code was passed.
        if ref:
            referral = await find_referral_by_code(db, ref)
            if referral and referral.referee_shop_id is None:
                await consume_referral_on_signup(db, referral, shop)

    _set_session_cookie(response, issue_session_token(shop.id))
    return {"ok": True, "shop_id": str(shop.id)}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


def _start_line_oauth(role: str, next_redeem: Optional[str] = None) -> RedirectResponse:
    """Common OAuth kickoff — only the role-tagged state cookie + optional
    next_redeem hint differ between the shop and customer flows."""
    if not line_is_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "LINE Login not configured (set LINE_CHANNEL_ID + LINE_CHANNEL_SECRET in .env)",
        )

    nonce, cookie_token = make_oauth_state(role=role, next_redeem=next_redeem)
    redirect = RedirectResponse(
        url=build_authorize_url(nonce), status_code=status.HTTP_302_FOUND
    )
    redirect.set_cookie(
        key=LINE_STATE_COOKIE,
        value=cookie_token,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        max_age=600,  # matches OAUTH_STATE_TTL_MINUTES
        path="/auth/line",
    )
    return redirect


@router.get("/line/start")
async def line_start():
    """Shop-side LINE Login: generate state, set cookie, redirect to LINE."""
    return _start_line_oauth(role="shop")


@router.get("/line/customer/start")
async def line_customer_start(next_redeem: Optional[str] = None):
    """Customer-side LINE Login — opened from the C2.signup picker. Returns
    the user to /my-cards on success with their guest cookie promoted to a
    claimed account. If `next_redeem=<shop_id>` is set (the C4 gate passes
    it), the callback fires the redemption and lands on /card/{shop}/claimed
    instead — auto-resuming the redeem the guest was trying to do."""
    return _start_line_oauth(role="customer", next_redeem=next_redeem)


@router.get("/line/callback")
async def line_callback(
    code: str,
    state: str,
    line_oauth_state: Optional[str] = Cookie(None, alias=LINE_STATE_COOKIE),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """LINE redirected back with code + state. Verify, exchange, then branch
    on the role embedded in the state cookie:
      - role=shop     → find/create Shop, set shop session cookie, → /shop/dashboard
      - role=customer → claim/merge the anonymous Customer with line_id,
                        refresh customer cookie, → /my-cards
                        (or /card/{shop}/claimed if next_redeem was set)
    """
    payload = verify_oauth_state(state, line_oauth_state)
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid OAuth state")

    role = payload["role"]
    next_redeem = payload.get("next_redeem")

    try:
        tokens = await exchange_code_for_token(code)
        profile = await fetch_profile(tokens["access_token"])
    except LineLoginError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    line_id = profile["userId"]
    display_name = profile.get("displayName") or None

    if role == "customer":
        anon, _ = await find_or_create_customer(customer_cookie, db)
        claimed = await claim_by_line(db, anon, line_id, display_name=display_name)

        target_url = await _redeem_after_claim(db, claimed, next_redeem) or "/my-cards"
        redirect = RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)
        set_customer_cookie(redirect, claimed.id)
        redirect.delete_cookie(LINE_STATE_COOKIE, path="/auth/line")
        return redirect

    # role == "shop" (default — backwards compatible with existing flow)
    result = await db.exec(select(Shop).where(Shop.line_id == line_id))
    shop = result.first()
    if not shop:
        shop = Shop(line_id=line_id, name=display_name or "Shop")
        db.add(shop)
        await db.commit()
        await db.refresh(shop)

    redirect = RedirectResponse(url="/shop/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(redirect, issue_session_token(shop.id))
    redirect.delete_cookie(LINE_STATE_COOKIE, path="/auth/line")
    return redirect


async def _redeem_after_claim(db: AsyncSession, customer: Customer, next_redeem: Optional[str]):
    """If `next_redeem` is a valid shop id and the just-claimed customer has
    a full card there, fire the redemption and return /card/{shop}/claimed?r=...
    so the caller redirects to C5 directly. Returns None on any failure
    (caller falls back to /my-cards) — auto-resume is best-effort, never
    fails the LINE/OTP flow itself."""
    if not next_redeem:
        return None
    try:
        from uuid import UUID as _UUID
        shop_id = _UUID(next_redeem)
    except ValueError:
        return None
    shop = await db.get(Shop, shop_id)
    if not shop:
        return None
    from app.services.events import feed_row_html, publish
    from app.services.redemption import RedemptionError, redeem
    try:
        redemption = await redeem(db, shop, customer)
    except RedemptionError:
        return None
    publish(
        shop.id,
        "feed-row",
        feed_row_html("redemption", redemption.id, redemption.created_at.strftime("%H:%M"), customer.display_name or "ลูกค้า"),
    )
    return f"/card/{shop.id}/claimed?r={redemption.id}"


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,  # always Secure — local dev uses HTTPS (mkcert), prod uses HTTPS
        samesite="lax",
        max_age=settings.session_expire_days * 24 * 3600,
        path="/",
    )
