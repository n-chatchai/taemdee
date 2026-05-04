"""Pairing routes — /auth/pair/start, /events, /redeem.

See docs/pwa-oauth-pairing.md for the flow diagram."""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, Response, status
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

    Hard rule: connect never creates a User. The customer cookie MUST
    already be present (PWA bootstrapping happens at /my-cards et al.,
    which call find_or_create_customer). Missing cookie → 401; we
    refuse to start a connect without a known originator."""
    import logging
    log = logging.getLogger(__name__)
    originator_id = decode_customer_token(customer_cookie) if customer_cookie else None
    if originator_id is None:
        log.error("pair/start: rejected — customer cookie missing or invalid")
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "ยังไม่ได้เข้าสู่ระบบในแอป · เปิดหน้าหลักก่อนแล้วลองใหม่",
        )
    log.info("pair/start: originator_customer_id=%s", originator_id)
    row = await create_pairing(db, originator_customer_id=originator_id)
    # Return pwa_token in the body. We used to set it as an HttpOnly
    # cookie scoped to /auth/pair, but iOS PWA standalone mode
    # sometimes drops that cookie across the window.open → Safari →
    # PWA-resume hand-off, breaking /redeem with "pwa_token cookie
    # missing". Returning it in the body lets the PWA stash it in
    # memory and pass it back explicitly on redeem — no cookie scope
    # to fight with.
    return JSONResponse({
        "code": row.code,
        "pwa_token": row.pwa_token,
        "expires_at": row.expires_at.isoformat() + "Z",
    })


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
    pwa_token_form: Optional[str] = Form(None, alias="pwa_token"),
    pwa_token_cookie: Optional[str] = Cookie(None, alias=PWA_TOKEN_COOKIE),
    db: AsyncSession = Depends(get_session),
):
    """Verify the pwa_token from the body (preferred — survives iOS
    PWA cookie isolation) or the legacy cookie, mark the row redeemed,
    and set the customer cookie on the response."""
    pwa_token = pwa_token_form or pwa_token_cookie
    row = await redeem_pairing(db, code, pwa_token)
    if row is None or row.customer_id is None:
        return Response(status_code=status.HTTP_410_GONE)
    body = JSONResponse({"ok": True})
    set_customer_cookie(body, row.customer_id)
    return body
