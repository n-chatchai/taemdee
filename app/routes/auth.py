from datetime import timedelta
from typing import Optional
from urllib.parse import quote, unquote
from loguru import logger
from jose import jwt


from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Form,
    HTTPException,
    Request,
    Response,
    status,
)
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
from app.models.util import utcnow
from app.core.templates import templates
from app.models import Customer, CustomerShopMute, Shop
from app.services.auth import decode_customer_token, generate_and_send_otp, issue_session_token, verify_otp
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
    request: Request,
    phone: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    # Determine role based on host
    host = request.headers.get("host", "").split(":")[0]
    is_shop_host = host.startswith("shop.") or host == settings.shop_domain
    role = "shop" if is_shop_host else "customer"

    if not settings.is_login_enabled(role, "phone"):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Phone login disabled for {role}s")
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

    # Detect "fresh signup" before the resolver runs — referral
    # binding fires only on first new shop. resolve_shop_signin is
    # idempotent for existing rows but also creates the Shop on first
    # signup, so we record the pre-state to know which side ran.
    from app.services.team import resolve_shop_signin

    pre_existing = (
        await db.exec(select(Shop).where(Shop.phone == phone))
    ).first()

    display_name = name if name and name != "New Shop" else None
    shop, staff_match = await resolve_shop_signin(
        db, "phone", phone, display_name=display_name,
    )

    if pre_existing is None and ref:
        referral = await find_referral_by_code(db, ref)
        if referral and referral.referee_shop_id is None:
            await consume_referral_on_signup(db, referral, shop)

    _set_session_cookie(
        response,
        issue_session_token(shop.id, staff_id=staff_match.id, is_owner=staff_match.is_owner),
    )
    return {"ok": True, "shop_id": str(shop.id)}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


def _connect_customer_id_from_cookie(customer_cookie: Optional[str]) -> Optional[str]:
    """Decode customer_cookie → customer_id string for baking into the
    OAuth state JWT. Same-window OAuth navigates within the PWA's own
    cookie jar, so the cookie is right there at /start. The state JWT
    still carries connect_customer_id explicitly so the callback can
    bind onto the existing customer even if the cookie somehow doesn't
    travel through the OAuth round-trip."""
    if not customer_cookie:
        return None
    cid = decode_customer_token(customer_cookie)
    return str(cid) if cid is not None else None


def _start_line_oauth(
    role: str,
    next_redeem: Optional[str] = None,
    connect_customer_id: Optional[str] = None,
) -> RedirectResponse:
    """Build the LINE OAuth state JWT and redirect to LINE."""
    if not line_is_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "LINE Login not configured (set LINE_CHANNEL_ID + LINE_CHANNEL_SECRET in .env)",
        )

    state_jwt = make_oauth_state(
        role=role, next_redeem=next_redeem,
        connect_customer_id=connect_customer_id,
    )
    return RedirectResponse(
        url=build_authorize_url(state_jwt), status_code=status.HTTP_302_FOUND
    )


@router.get("/line/start")
async def line_start():
    """Shop-side LINE Login: generate state, set cookie, redirect to LINE."""
    if not settings.is_login_enabled("shop", "line"):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LINE login disabled for shops")
    return _start_line_oauth(role="shop")


@router.get("/line/customer/start")
async def line_customer_start(
    next_redeem: Optional[str] = None,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
):
    """Customer-side LINE OAuth kickoff. Reads the customer cookie that
    travels with the same-window navigation and bakes the customer id
    into the OAuth state so the callback binds onto the SAME user."""
    if not settings.is_login_enabled("customer", "line"):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LINE login disabled for customers")
    return _start_line_oauth(
        role="customer", next_redeem=next_redeem,
        connect_customer_id=_connect_customer_id_from_cookie(customer_cookie),
    )


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
        parts = [unquote(p) for p in c3_line_ctx.split("|||")]
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
        customer.user.display_name = display_name.strip()
        db.add(customer.user)
        await db.commit()

    target_shop_id: Optional[_uuid.UUID] = None
    if next_redeem:
        try:
            target_shop_id = _uuid.UUID(next_redeem)
        except ValueError:
            target_shop_id = None

    if dr_consent != "on" and target_shop_id:
        existing_mute = (
            await db.exec(
                select(CustomerShopMute).where(
                    CustomerShopMute.customer_id == customer.id,
                    CustomerShopMute.shop_id == target_shop_id,
                )
            )
        ).first()
        if not existing_mute:
            db.add(CustomerShopMute(customer_id=customer.id, shop_id=target_shop_id))
            await db.commit()

    target_url = await _redeem_after_claim(db, customer, next_redeem) or "/my-cards"
    response = RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("c3_line_ctx", path="/")
    return response


@router.get("/line/callback")
async def line_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    line_oauth_state: Optional[str] = Cookie(None, alias=LINE_STATE_COOKIE),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
    transfer: Optional[str] = None,
):
    """LINE redirected back with code + state. Verify, exchange, then branch
    on the role embedded in the state cookie:
      - role=shop     → find/create Shop, set shop session cookie, → /shop/dashboard
      - role=customer → claim/merge the anonymous Customer with line_id,
                        refresh customer cookie, → /my-cards
                        (or /card/{shop}/claimed if next_redeem was set)
    """
    # 1. Transfer Logic: If we received a transfer token from the main domain, 
    # verify it and proceed directly to login (skipping state check).
    if transfer:
        try:
            payload = jwt.decode(transfer, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            if payload.get("sub") != "auth_transfer":
                raise ValueError("Invalid transfer sub")
            line_id = payload["line_id"]
            display_name = payload.get("display_name")
            picture_url = payload.get("picture_url")
            role = payload.get("role", "shop")
            logger.success(f"✅ Transfer Token Verified | role={role}")
            connect_customer_id = None
            # Skip to the final login part
            goto_login = True
        except Exception as e:
            logger.error(f"❌ Transfer Token Failed: {e}")
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid transfer token")
    else:
        # Standard flow: Verify state
        payload = verify_oauth_state(state, line_oauth_state)
        if not payload:
            logger.error(f"❌ Invalid OAuth state | state={'present' if state else 'missing'} | cookie={'present' if line_oauth_state else 'missing'}")
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid OAuth state")

        role = payload["role"]
        next_redeem = payload.get("next_redeem")
        connect_customer_id = payload.get("connect_customer_id")
        goto_login = False

    # 2. Dispatcher Logic: If we are on the main domain but this is a shop login,
    # exchange the code HERE, then bounce to the shop domain with a Transfer Token.
    host = request.headers.get("host", "").split(":")[0]
    is_main_host = host == settings.main_domain or not (
        host.startswith("shop.") or host == settings.shop_domain
    )

    logger.info(f"🌐 Dispatcher Logic: host={host} | role={role} | is_main_host={is_main_host}")

    if not transfer and role == "shop" and is_main_host:
        # Step A: Exchange code on the main domain where we have the state cookie
        if not code:
             raise HTTPException(status.HTTP_400_BAD_REQUEST, "Code missing for shop login")
        try:
            tokens = await exchange_code_for_token(code)
            profile = await fetch_profile(tokens["access_token"])
        except LineLoginError as e:
            logger.error(f"❌ Token Exchange Failed on Main: {e}")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

        # Step B: Create a Transfer Token (short-lived JWT)
        transfer_payload = {
            "sub": "auth_transfer",
            "line_id": profile["userId"],
            "display_name": profile.get("displayName"),
            "picture_url": profile.get("pictureUrl"),
            "role": "shop",
            "exp": utcnow() + timedelta(minutes=2),
        }
        token = jwt.encode(transfer_payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
        
        shop_host = settings.shop_domain if settings.environment == "production" else f"shop.{host}"
        target_url = f"https://{shop_host}/auth/line/callback?transfer={token}"
        logger.warning(f"↪️ Bouncing Shop Owner to: https://{shop_host}/auth/line/callback?transfer=...")
        return RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)

    # 3. Proceed with login if not already done via transfer.
    if not goto_login:
        try:
            tokens = await exchange_code_for_token(code)
            profile = await fetch_profile(tokens["access_token"])
        except LineLoginError as e:
            logger.error(f"❌ Token Exchange Failed: {e}")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

        line_id = profile["userId"]
        display_name = profile.get("displayName") or None
        picture_url = profile.get("pictureUrl") or None

    if role == "customer":
        anon = await _connect_originator_or_cookie(
            db, connect_customer_id, customer_cookie,
        )
        onboard_name = (
            anon.display_name
        )  # Name from onboard.greet (before LINE overwrites)
        # Already-claimed customer linking a 2nd provider goes through
        # link_to_claimed (refuses on identity conflict). Anonymous
        # rows still go through the original claim path.
        if anon.is_anonymous:
            claimed = await claim_by_line(
                db, anon, line_id, display_name=display_name, picture_url=picture_url
            )
        else:
            from app.services.soft_wall import IdentityConflict, link_to_claimed
            try:
                claimed = await link_to_claimed(
                    db, anon, line_id=line_id,
                    display_name=display_name, picture_url=picture_url,
                )
            except IdentityConflict as e:
                raise HTTPException(status.HTTP_409_CONFLICT, str(e))

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
        redirect = RedirectResponse(
            url=target_url, status_code=status.HTTP_303_SEE_OTHER
        )
        # Cookie values must round-trip through latin-1 (Set-Cookie header
        # encoding), so percent-encode each piece — display_name from LINE
        # is typically Thai. Reader does unquote() on the same boundary.
        ctx_value = "|||".join(
            quote(s or "", safe="")
            for s in (display_name, onboard_name, picture_url)
        )
        redirect.set_cookie(
            key="c3_line_ctx",
            value=ctx_value,
            httponly=True,
            path="/",
        )
        set_customer_cookie(redirect, claimed.id)
        redirect.delete_cookie(LINE_STATE_COOKIE, path="/auth/line")
        return redirect

    # role == "shop". Generic resolver handles: existing owner-staff
    # match → just sign in; pending invite → accept_invite + sign in;
    # no staff but Shop has matching line_id (pre-unification) → lazy-
    # create owner-staff; fully new → create Shop + owner-staff.
    from app.services.team import resolve_shop_signin

    shop, staff_match = await resolve_shop_signin(
        db, "line", line_id,
        display_name=display_name, picture_url=picture_url,
    )

    redirect = RedirectResponse(
        url="/shop/dashboard", status_code=status.HTTP_303_SEE_OTHER
    )
    _set_session_cookie(
        redirect,
        issue_session_token(shop.id, staff_id=staff_match.id, is_owner=staff_match.is_owner),
    )
    redirect.delete_cookie(LINE_STATE_COOKIE, path="/auth/line")
    return redirect


def _start_google_oauth(
    role: str,
    next_redeem: Optional[str] = None,
    connect_customer_id: Optional[str] = None,
) -> RedirectResponse:
    if not settings.is_login_enabled(role, "google") or not google_login.is_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Google Login disabled or not configured",
        )
    state_jwt = make_oauth_state(
        role=role, next_redeem=next_redeem,
        connect_customer_id=connect_customer_id,
    )
    return RedirectResponse(
        url=google_login.build_authorize_url(state_jwt),
        status_code=status.HTTP_302_FOUND,
    )


def _start_facebook_oauth(
    role: str,
    next_redeem: Optional[str] = None,
    connect_customer_id: Optional[str] = None,
) -> RedirectResponse:
    if not settings.is_login_enabled(role, "facebook") or not facebook_login.is_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Facebook Login disabled or not configured",
        )
    state_jwt = make_oauth_state(
        role=role, next_redeem=next_redeem,
        connect_customer_id=connect_customer_id,
    )
    return RedirectResponse(
        url=facebook_login.build_authorize_url(state_jwt),
        status_code=status.HTTP_302_FOUND,
    )


async def _connect_originator_or_cookie(
    db: AsyncSession,
    connect_customer_id: Optional[str],
    customer_cookie: Optional[str],
) -> Customer:
    """Resolve the customer for a customer-side OAuth callback.

    Hard rule: NEVER spawn a new User during a connect. Bind onto the
    customer we already know about, or refuse loudly.

      - connect_customer_id (signed into OAuth state at /start) → use it.
      - else customer_cookie (browser-mode flow, cookie travels with
        the OAuth round-trip) → use it.
      - else: refuse. We will not call find_or_create_customer here —
        that would forge a fresh User exactly when we promised not to.
    """
    from uuid import UUID

    if connect_customer_id:
        try:
            cid = UUID(connect_customer_id)
        except (ValueError, TypeError):
            logger.error(
                "connect_originator: connect_customer_id=%s not a UUID",
                connect_customer_id,
            )
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "บัญชีต้นทางไม่ถูกต้อง · ลองออกแล้วเข้าใหม่",
            )
        existing = await db.get(Customer, cid)
        if existing is None:
            logger.error(
                "connect_originator: customer_id=%s not in DB", cid,
            )
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "ไม่พบบัญชีต้นทาง · ลองออกแล้วเข้าใหม่",
            )
        logger.info(
            "connect_originator: from state customer=%s user=%s",
            existing.id, existing.user_id,
        )
        return existing

    if customer_cookie:
        from app.services.auth import decode_customer_token
        cid = decode_customer_token(customer_cookie)
        if cid is None:
            logger.error("connect_originator: cookie present but undecodable")
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "บัญชีต้นทางไม่ถูกต้อง · ลองออกแล้วเข้าใหม่",
            )
        existing = await db.get(Customer, cid)
        if existing is None:
            logger.error("connect_originator: cookie customer_id=%s not in DB", cid)
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "ไม่พบบัญชีต้นทาง · ลองออกแล้วเข้าใหม่",
            )
        logger.info(
            "connect_originator: from cookie customer=%s user=%s",
            existing.id, existing.user_id,
        )
        return existing

    logger.error(
        "connect_originator: refused — no connect_customer_id and no cookie"
    )
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "ไม่พบบัญชีที่จะเชื่อม · เปิดหน้าหลักก่อนแล้วลองใหม่",
    )


async def _resolve_customer_oauth(
    db: AsyncSession,
    customer_cookie: Optional[str],
    provider: str,
    ext_id: str,
    *,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
    connect_customer_id: Optional[str] = None,
):
    """Customer-side OAuth finalizer — shared by /auth/{line,google,
    facebook}/callback. Returns the claimed customer; caller sets the
    cookie and picks the redirect target."""
    from app.services.soft_wall import (
        IdentityConflict,
        claim_by_provider,
        link_to_claimed,
    )

    anon = await _connect_originator_or_cookie(
        db, connect_customer_id, customer_cookie,
    )
    if anon.is_anonymous:
        return await claim_by_provider(
            db, anon, provider, ext_id,
            display_name=display_name, picture_url=picture_url,
        )
    try:
        return await link_to_claimed(
            db, anon,
            provider=provider, ext_id=ext_id,
            display_name=display_name, picture_url=picture_url,
        )
    except IdentityConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.get("/google/start")
async def google_start():
    """Shop-side Google Login."""
    return _start_google_oauth(role="shop")


@router.get("/google/customer/start")
async def google_customer_start(
    next_redeem: Optional[str] = None,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
):
    """Customer-side Google Sign-In. Bakes the customer cookie's id
    into the OAuth state so the callback binds onto the SAME user."""
    return _start_google_oauth(
        role="customer", next_redeem=next_redeem,
        connect_customer_id=_connect_customer_id_from_cookie(customer_cookie),
    )


@router.get("/facebook/start")
async def facebook_start():
    """Shop-side Facebook Login."""
    return _start_facebook_oauth(role="shop")


@router.get("/facebook/customer/start")
async def facebook_customer_start(
    next_redeem: Optional[str] = None,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
):
    """Customer-side Facebook Login. Bakes the customer cookie's id
    into the OAuth state so the callback binds onto the SAME user."""
    return _start_facebook_oauth(
        role="customer", next_redeem=next_redeem,
        connect_customer_id=_connect_customer_id_from_cookie(customer_cookie),
    )


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str,
    state: str,
    google_oauth_state: Optional[str] = Cookie(None, alias=GOOGLE_STATE_COOKIE),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Google OAuth came back. Customer flow claims-or-links the
    Customer; shop flow runs the StaffMember-first resolver so the
    owner can sign in via Google (and any pending invite gets
    accepted on first match).
    """
    payload = verify_oauth_state(state, google_oauth_state)
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid OAuth state")

    role = payload.get("role", "customer")
    next_redeem = payload.get("next_redeem")
    connect_customer_id = payload.get("connect_customer_id")

    try:
        tokens = await google_login.exchange_code_for_token(code)
        profile = await google_login.fetch_profile(tokens["access_token"])
    except google_login.GoogleLoginError as e:
        logger.error(f"❌ Google Token Exchange Failed: {e}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    google_id = profile.get("sub")
    if not google_id:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Google userinfo missing 'sub'"
        )
    display_name = profile.get("name") or None
    picture_url = profile.get("picture") or None

    if role == "shop":
        from app.services.team import resolve_shop_signin

        shop, staff_match = await resolve_shop_signin(
            db, "google", google_id,
            display_name=display_name, picture_url=picture_url,
        )
        redirect = RedirectResponse(
            url="/shop/dashboard", status_code=status.HTTP_303_SEE_OTHER
        )
        _set_session_cookie(
            redirect,
            issue_session_token(
                shop.id, staff_id=staff_match.id, is_owner=staff_match.is_owner
            ),
        )
        redirect.delete_cookie(GOOGLE_STATE_COOKIE, path="/auth/google")
        return redirect

    # role == "customer"
    claimed = await _resolve_customer_oauth(
        db, customer_cookie,
        "google", google_id,
        display_name=display_name, picture_url=picture_url,
        connect_customer_id=connect_customer_id,
    )

    target_url = await _redeem_after_claim(db, claimed, next_redeem) or "/my-cards"
    redirect = RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)
    set_customer_cookie(redirect, claimed.id)
    redirect.delete_cookie(GOOGLE_STATE_COOKIE, path="/auth/google")
    return redirect


@router.get("/facebook/callback")
async def facebook_callback(
    request: Request,
    code: str,
    state: str,
    facebook_oauth_state: Optional[str] = Cookie(None, alias=FACEBOOK_STATE_COOKIE),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Facebook OAuth came back. Customer flow claims-or-links the
    Customer; shop flow runs the StaffMember-first resolver. The
    `email` field may be missing if the user denied that permission —
    we don't rely on it, only display_name + the `id` claim.
    """
    payload = verify_oauth_state(state, facebook_oauth_state)
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid OAuth state")

    role = payload.get("role", "customer")
    next_redeem = payload.get("next_redeem")
    connect_customer_id = payload.get("connect_customer_id")

    try:
        tokens = await facebook_login.exchange_code_for_token(code)
        profile = await facebook_login.fetch_profile(tokens["access_token"])
    except facebook_login.FacebookLoginError as e:
        logger.error(f"❌ Facebook Token Exchange Failed: {e}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    facebook_id = profile.get("id")
    if not facebook_id:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Facebook profile missing 'id'"
        )
    display_name = profile.get("name") or None
    picture_url = (
        profile.get("picture", {}).get("data", {}).get("url")
        if isinstance(profile.get("picture"), dict)
        else None
    )

    if role == "shop":
        from app.services.team import resolve_shop_signin

        shop, staff_match = await resolve_shop_signin(
            db, "facebook", facebook_id,
            display_name=display_name, picture_url=picture_url,
        )
        redirect = RedirectResponse(
            url="/shop/dashboard", status_code=status.HTTP_303_SEE_OTHER
        )
        _set_session_cookie(
            redirect,
            issue_session_token(
                shop.id, staff_id=staff_match.id, is_owner=staff_match.is_owner
            ),
        )
        redirect.delete_cookie(FACEBOOK_STATE_COOKIE, path="/auth/facebook")
        return redirect

    # role == "customer"
    claimed = await _resolve_customer_oauth(
        db, customer_cookie,
        "facebook", facebook_id,
        display_name=display_name, picture_url=picture_url,
        connect_customer_id=connect_customer_id,
    )

    target_url = await _redeem_after_claim(db, claimed, next_redeem) or "/my-cards"
    redirect = RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)
    set_customer_cookie(redirect, claimed.id)
    redirect.delete_cookie(FACEBOOK_STATE_COOKIE, path="/auth/facebook")
    return redirect


async def _redeem_after_claim(
    db: AsyncSession, customer: Customer, next_redeem: Optional[str]
):
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
        feed_row_html(
            "redemption",
            redemption.id,
            bkk_feed_time(redemption.created_at),
            customer.display_name or "ลูกค้า",
        ),
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
