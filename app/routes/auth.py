from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME
from app.core.config import settings
from app.core.database import get_session
from app.models import Shop
from app.services.auth import generate_and_send_otp, issue_session_token, verify_otp
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
    await generate_and_send_otp(db, phone)
    return {"ok": True}


@router.post("/otp/verify")
async def verify_and_login(
    response: Response,
    phone: str = Form(...),
    code: str = Form(...),
    name: str = Form("New Shop"),
    ref: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_session),
):
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


@router.get("/line/start")
async def line_start():
    """Start LINE Login: generate state, set cookie, redirect to LINE."""
    if not line_is_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "LINE Login not configured (set LINE_CHANNEL_ID + LINE_CHANNEL_SECRET in .env)",
        )

    nonce, cookie_token = make_oauth_state()
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


@router.get("/line/callback")
async def line_callback(
    code: str,
    state: str,
    line_oauth_state: Optional[str] = Cookie(None, alias=LINE_STATE_COOKIE),
    db: AsyncSession = Depends(get_session),
):
    """LINE redirected back with code + state. Verify, exchange, find/create shop, login."""
    if not verify_oauth_state(state, line_oauth_state):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid OAuth state")

    try:
        tokens = await exchange_code_for_token(code)
        profile = await fetch_profile(tokens["access_token"])
    except LineLoginError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    line_id = profile["userId"]
    display_name = profile.get("displayName", "Shop")

    result = await db.exec(select(Shop).where(Shop.line_id == line_id))
    shop = result.first()
    if not shop:
        shop = Shop(line_id=line_id, name=display_name)
        db.add(shop)
        await db.commit()
        await db.refresh(shop)

    redirect = RedirectResponse(url="/shop/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(redirect, issue_session_token(shop.id))
    redirect.delete_cookie(LINE_STATE_COOKIE, path="/auth/line")
    return redirect


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        max_age=settings.session_expire_days * 24 * 3600,
        path="/",
    )
