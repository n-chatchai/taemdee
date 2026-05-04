"""Pairing routes — /auth/pair/start, /events, /redeem.

See docs/pwa-oauth-pairing.md for the flow diagram."""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import CUSTOMER_COOKIE_NAME, set_customer_cookie
from app.core.database import get_session
from app.services.auth import decode_customer_token
from app.services.pairing import (
    PAIRING_TTL_MINUTES,
    PWA_TOKEN_COOKIE,
    create_pairing,
    drop_local_event,
    find_active_pairing,
    get_or_create_local_event,
    redeem_pairing,
)

router = APIRouter()


@router.post("/auth/pair/start")
async def pair_start(
    response: Response,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Mint a Pairing code, set the pwa_token cookie on the PWA, return
    the code in the body. Client uses the code in the OAuth `?pair=`
    URL and again when calling /redeem.

    The PWA's customer cookie (if present) is decoded into
    `originator_customer_id` on the row — the OAuth callback uses that
    to bind the new provider to the SAME customer/user, instead of
    spawning a fresh anonymous row in the cookie-less system browser
    that would otherwise overwrite the PWA's session at /redeem."""
    import logging
    log = logging.getLogger(__name__)
    originator_id = decode_customer_token(customer_cookie) if customer_cookie else None
    if originator_id is None:
        # Hard rule: a connect flow can only START from a known customer.
        # Without an originator, the OAuth callback in the system browser
        # would have to mint a fresh User — exactly what we never want
        # to happen on a connect. Refuse here so the PWA can surface a
        # "log in first" prompt instead of silently forking the account.
        log.error(
            "pair/start: rejected — customer cookie missing or invalid"
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "ยังไม่ได้เข้าสู่ระบบในแอป · ลองออกแล้วเข้าใหม่",
        )
    log.info("pair/start: originator_customer_id=%s", originator_id)
    row = await create_pairing(db, originator_customer_id=originator_id)
    body = JSONResponse({
        "code": row.code,
        "expires_at": row.expires_at.isoformat() + "Z",
    })
    body.set_cookie(
        key=PWA_TOKEN_COOKIE,
        value=row.pwa_token,
        httponly=True,
        secure=True,  # always Secure — local dev uses HTTPS via mkcert
        samesite="lax",
        max_age=PAIRING_TTL_MINUTES * 60,
        path="/auth/pair",
    )
    return body


@router.get("/auth/pair/{code}/events")
async def pair_events(
    request: Request,
    code: str,
    db: AsyncSession = Depends(get_session),
):
    """SSE — emits one `claimed` event when the OAuth callback fills in
    customer_id, then closes. Falls back to a slow DB poll so multi-worker
    setups still see the claim even when the in-process asyncio.Event was
    set in a different worker."""
    row = await find_active_pairing(db, code)
    if row is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    async def stream():
        ev = get_or_create_local_event(code)
        keepalive_at = asyncio.get_event_loop().time()
        try:
            while True:
                if await request.is_disconnected():
                    return
                # Re-fetch the row each iteration so a claim from another
                # worker (no in-process Event fire) still gets noticed.
                fresh = None
                async for db2 in get_session():
                    fresh = await find_active_pairing(db2, code)
                    break
                if fresh is None:
                    yield "event: expired\ndata: {}\n\n"
                    return
                if fresh.customer_id is not None:
                    payload = json.dumps({"provider": fresh.provider or ""})
                    yield f"event: claimed\ndata: {payload}\n\n"
                    return
                # Wait up to 2s for the in-process Event, otherwise loop
                # and re-check the DB. Send a keepalive comment every 15s
                # so proxies don't kill the stream.
                try:
                    await asyncio.wait_for(ev.wait(), timeout=2.0)
                    ev.clear()
                except asyncio.TimeoutError:
                    pass
                now = asyncio.get_event_loop().time()
                if now - keepalive_at >= 15:
                    yield ": keepalive\n\n"
                    keepalive_at = now
        finally:
            drop_local_event(code)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/auth/pair/{code}/redeem")
async def pair_redeem(
    code: str,
    response: Response,
    pwa_token: Optional[str] = Cookie(None, alias=PWA_TOKEN_COOKIE),
    db: AsyncSession = Depends(get_session),
):
    """Verify the pwa_token cookie matches the Pairing row, mark it
    redeemed, and set the customer cookie on the response (lands in
    the PWA's cookie store since this request was issued from the PWA)."""
    row = await redeem_pairing(db, code, pwa_token)
    if row is None or row.customer_id is None:
        return Response(status_code=status.HTTP_410_GONE)
    body = JSONResponse({"ok": True})
    set_customer_cookie(body, row.customer_id)
    body.delete_cookie(PWA_TOKEN_COOKIE, path="/auth/pair")
    return body
