from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, Response, status
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
from app.core.templates import templates
from app.models import Customer, CustomerShopMute, Shop
from app.services.auth import generate_and_send_otp, issue_session_token, verify_otp
from app.services.soft_wall import claim_by_facebook, claim_by_google, claim_by_line
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
from app.services import google_login, facebook_login

router = APIRouter()

LINE_STATE_COOKIE = "line_oauth_state"
GOOGLE_STATE_COOKIE = "google_oauth_state"
FACEBOOK_STATE_COOKIE = "facebook_oauth_state"


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
    """Customer-side LINE Login — opened from the C2.signup picker. After
    LINE OAuth comes back, the callback hands off to /auth/line/customer/confirm
    (C3.line) so the customer can review the bound LINE handle and toggle
    DeeReach consent before landing on /my-cards (or /card/{shop}/claimed
    if `next_redeem=<shop_id>` is set — auto-resumes the redeem the guest
    was trying to do at the C4 gate)."""
    return _start_line_oauth(role="customer", next_redeem=next_redeem)


@router.get("/line/customer/confirm")
async def line_customer_confirm(
    request: Request,
    next_redeem: Optional[str] = None,
    c3_line_ctx: Optional[str] = Cookie(None, alias="c3_line_ctx"),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """C3.line — confirmation step shown after the LINE OAuth callback.

    Renders the LINE display name + DeeReach consent toggle. Reachable
    only with a customer cookie that already points at the just-claimed
    Customer (the callback set it). If the cookie is missing or stale we
    bounce back to /my-cards rather than render an empty page."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    if customer.is_anonymous:
        return RedirectResponse(url="/my-cards", status_code=status.HTTP_303_SEE_OTHER)

    # Parse the context cookie set by the callback (line_name|||onboard_name|||picture_url)
    line_name = customer.display_name
    onboard_name = None
    picture_url = None
    if c3_line_ctx:
        parts = c3_line_ctx.split("|||")
        if len(parts) >= 3:
            line_name = parts[0] or None
            onboard_name = parts[1] or None
            picture_url = parts[2] or None

    return templates.TemplateResponse(
        request=request,
        name="c3_line.html",
        context={
            "line_display_name": line_name,
            "onboard_name": onboard_name,
            "picture_url": picture_url,
            "next_redeem": next_redeem,
        },
    )


@router.post("/line/customer/confirm")
async def line_customer_confirm_save(
    next_redeem: Optional[str] = Form(None),
    display_name: Optional[str] = Form(None),
    dr_consent: Optional[str] = Form("on"),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Persist the DeeReach consent toggle from C3.line, then land the
    customer on /my-cards (or /card/{shop}/claimed if next_redeem points
    at a shop with a full card waiting to be redeemed). Mirrors the same
    consent semantics as /card/claim/phone — 'off' writes a per-shop mute
    row for `next_redeem`, 'on' is the no-op default."""
    import uuid as _uuid

    customer, _ = await find_or_create_customer(customer_cookie, db)
    if customer.is_anonymous:
        return RedirectResponse(url="/my-cards", status_code=status.HTTP_303_SEE_OTHER)

    # Allow customer to override the LINE display_name
    if display_name and display_name.strip():
        customer.display_name = display_name.strip()
        await db.commit()

    target_shop_id: Optional[_uuid.UUID] = None
    if next_redeem:
        try:
            target_shop_id = _uuid.UUID(next_redeem)
        except ValueError:
            target_shop_id = None

    if dr_consent != "on" and target_shop_id:
        existing_mute = (await db.exec(
            select(CustomerShopMute).where(
                CustomerShopMute.customer_id == customer.id,
                CustomerShopMute.shop_id == target_shop_id,
            )
        )).first()
        if not existing_mute:
            db.add(CustomerShopMute(customer_id=customer.id, shop_id=target_shop_id))
            await db.commit()

    target_url = await _redeem_after_claim(db, customer, next_redeem) or "/my-cards"
    response = RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("c3_line_ctx", path="/")
    return response


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
    picture_url = profile.get("pictureUrl") or None

    if role == "customer":
        anon, _ = await find_or_create_customer(customer_cookie, db)
        onboard_name = anon.display_name  # Name from onboard.greet (before LINE overwrites)
        claimed = await claim_by_line(db, anon, line_id, display_name=display_name, picture_url=picture_url)

        # Hand off to C3.line — design splits "LINE OAuth came back" from
        # "decide DeeReach consent". Carry next_redeem through as a query
        # arg so the confirm POST can still auto-resume the redeem flow.
        # Cookie still has to be refreshed here: claim_by_line may merge
        # the anonymous Customer into an existing claimed row (different
        # id), and without the new cookie subsequent requests would land
        # on a phantom anon.
        target_url = "/auth/line/customer/confirm"
        if next_redeem:
            target_url += f"?next_redeem={next_redeem}"
        redirect = RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)
        redirect.set_cookie(
            key="c3_line_ctx",
            value=f"{display_name or ''}|||{onboard_name or ''}|||{picture_url or ''}",
            httponly=True,
            path="/",
        )
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


def _start_google_oauth(role: str, next_redeem: Optional[str] = None) -> RedirectResponse:
    if not google_login.is_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Google Login not configured (set GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET in .env)",
        )
    nonce, cookie_token = make_oauth_state(role=role, next_redeem=next_redeem)
    redirect = RedirectResponse(
        url=google_login.build_authorize_url(nonce), status_code=status.HTTP_302_FOUND
    )
    redirect.set_cookie(
        key=GOOGLE_STATE_COOKIE,
        value=cookie_token,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        max_age=600,
        path="/auth/google",
    )
    return redirect


def _start_facebook_oauth(role: str, next_redeem: Optional[str] = None) -> RedirectResponse:
    if not facebook_login.is_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Facebook Login not configured (set FACEBOOK_APP_ID + FACEBOOK_APP_SECRET in .env)",
        )
    nonce, cookie_token = make_oauth_state(role=role, next_redeem=next_redeem)
    redirect = RedirectResponse(
        url=facebook_login.build_authorize_url(nonce), status_code=status.HTTP_302_FOUND
    )
    redirect.set_cookie(
        key=FACEBOOK_STATE_COOKIE,
        value=cookie_token,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        max_age=600,
        path="/auth/facebook",
    )
    return redirect


@router.get("/google/customer/start")
async def google_customer_start(next_redeem: Optional[str] = None):
    """Customer-side Google Sign-In. Mirrors the LINE flow — start sets a
    short-lived state cookie + redirects to Google's consent screen.
    `?next_redeem=<shop_id>` carries through state and auto-resumes a C4
    redemption after the callback completes."""
    return _start_google_oauth(role="customer", next_redeem=next_redeem)


@router.get("/google/callback")
async def google_callback(
    code: str,
    state: str,
    google_oauth_state: Optional[str] = Cookie(None, alias=GOOGLE_STATE_COOKIE),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Google OAuth came back. Verify state, exchange code, claim the
    anonymous Customer with the `sub` from the userinfo endpoint, then
    bounce to /my-cards (or /card/{shop}/claimed if next_redeem is set).
    No C3.confirm step yet — DeeReach defaults to opt-in; consent UI
    will be a follow-up frontend pass."""
    payload = verify_oauth_state(state, google_oauth_state)
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid OAuth state")

    next_redeem = payload.get("next_redeem")

    try:
        tokens = await google_login.exchange_code_for_token(code)
        profile = await google_login.fetch_profile(tokens["access_token"])
    except google_login.GoogleLoginError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    google_id = profile.get("sub")
    if not google_id:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Google userinfo missing 'sub'")
    display_name = profile.get("name") or None

    anon, _ = await find_or_create_customer(customer_cookie, db)
    claimed = await claim_by_google(db, anon, google_id, display_name=display_name)

    target_url = await _redeem_after_claim(db, claimed, next_redeem) or "/my-cards"
    redirect = RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)
    set_customer_cookie(redirect, claimed.id)
    redirect.delete_cookie(GOOGLE_STATE_COOKIE, path="/auth/google")
    return redirect


@router.get("/facebook/customer/start")
async def facebook_customer_start(next_redeem: Optional[str] = None):
    """Customer-side Facebook Login. Same shape as Google/LINE."""
    return _start_facebook_oauth(role="customer", next_redeem=next_redeem)


@router.get("/facebook/callback")
async def facebook_callback(
    code: str,
    state: str,
    facebook_oauth_state: Optional[str] = Cookie(None, alias=FACEBOOK_STATE_COOKIE),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Facebook OAuth came back. Same shape as the Google callback —
    exchange code, claim by `id` from /me, redirect to /my-cards. The
    `email` field may be missing if the user denied that permission;
    we don't rely on it for the claim, only display_name."""
    payload = verify_oauth_state(state, facebook_oauth_state)
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid OAuth state")

    next_redeem = payload.get("next_redeem")

    try:
        tokens = await facebook_login.exchange_code_for_token(code)
        profile = await facebook_login.fetch_profile(tokens["access_token"])
    except facebook_login.FacebookLoginError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    facebook_id = profile.get("id")
    if not facebook_id:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Facebook profile missing 'id'")
    display_name = profile.get("name") or None

    anon, _ = await find_or_create_customer(customer_cookie, db)
    claimed = await claim_by_facebook(db, anon, facebook_id, display_name=display_name)

    target_url = await _redeem_after_claim(db, claimed, next_redeem) or "/my-cards"
    redirect = RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)
    set_customer_cookie(redirect, claimed.id)
    redirect.delete_cookie(FACEBOOK_STATE_COOKIE, path="/auth/facebook")
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
    from app.models.util import bkk_feed_time
    from app.services.events import feed_row_html, publish
    from app.services.redemption import RedemptionError, redeem
    try:
        redemption = await redeem(db, shop, customer)
    except RedemptionError:
        return None
    publish(
        shop.id,
        "feed-row",
        feed_row_html("redemption", redemption.id, bkk_feed_time(redemption.created_at), customer.display_name or "ลูกค้า"),
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
