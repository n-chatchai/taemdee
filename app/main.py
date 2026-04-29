from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import CUSTOMER_COOKIE_NAME, SESSION_COOKIE_NAME, SessionAuthError
from app.core.database import engine, get_session
from app.core.templates import ASSET_VERSION, templates
from app.models import Customer, Shop
from app.routes import auth, branches, customer, deereach, issuance, shops, team
from app.services.auth import decode_customer_token, decode_session_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed by Alembic — run `alembic upgrade head` before starting the server.
    # VAPID keys are bootstrapped by the RQ worker on its first boot
    # (services/web_push.ensure_vapid_keys); the web process reads them
    # lazily via load_vapid_keys when /push/vapid-public is hit.
    from app.services import events
    await events.start()
    try:
        yield
    finally:
        await events.stop()
        await engine.dispose()


app = FastAPI(title="TaemDee — Digital Point Cards", lifespan=lifespan)


@app.middleware("http")
async def revalidate_html_responses(request, call_next):
    """Force fresh HTML on every request — covers iOS Safari PWA tap-to-open
    where the standalone webview otherwise serves a stale cached page after
    a deploy.

    Use `no-cache, must-revalidate` (not `no-store`): browsers MUST hit the
    server before reusing the cached copy, but the page is still eligible
    for the in-memory bfcache used by back-forward / iOS swipe-back gesture.
    `no-store` would block bfcache too — making swipe-back a full reload,
    which feels slow and breaks View Transitions on the way back.

    Static assets (CSS/JS/images) keep their browser-cache because they're
    already URL-busted via ?v=<git-sha> on every link tag.
    """
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.exception_handler(SessionAuthError)
async def session_auth_error_handler(request: Request, exc: SessionAuthError):
    """When a session is missing/invalid/points at a deleted shop:

    - HTML browser requests → 303 redirect to /shop/login + clear the bad cookie.
      The destination page can show `?reason=...` if it ever wants to surface
      the specific cause.
    - JSON / API clients → standard 401 with the informative `detail` string.

    Detection is by exception TYPE (not string match), so detail messages can
    evolve freely without breaking the handler.
    """
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        response = RedirectResponse(
            url=f"/shop/login?reason={exc.reason}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        response.delete_cookie(SESSION_COOKIE_NAME, path="/")
        return response

    from fastapi.exception_handlers import http_exception_handler

    return await http_exception_handler(request, exc)


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def home(request: Request, db: AsyncSession = Depends(get_session)):
    """Marketing home for guests. Logged-in users get sent to their app —
    matters most for PWA installs (Add to Home Screen): the saved icon should
    open the user's dashboard, not the marketing pitch they've already seen.
    """
    from uuid import UUID

    shop_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if shop_cookie:
        payload = decode_session_token(shop_cookie)
        # Verify the referenced shop still exists before redirecting — otherwise
        # /shop/dashboard would just raise SessionAuthError → /shop/login, which
        # strands an anonymous customer behind a login wall they don't need.
        if payload and (shop_id := payload.get("shop_id")):
            try:
                if await db.get(Shop, UUID(shop_id)):
                    return RedirectResponse(url="/shop/dashboard", status_code=status.HTTP_303_SEE_OTHER)
            except ValueError:
                pass  # Malformed shop_id in token — fall through to customer check.

    customer_cookie = request.cookies.get(CUSTOMER_COOKIE_NAME)
    if customer_cookie:
        customer_id = decode_customer_token(customer_cookie)
        if customer_id and await db.get(Customer, customer_id):
            # Both claimed and guest customers land on /my-cards now — guests
            # see the same list with the green signup banner pinned at the
            # bottom (revised C7 design).
            return RedirectResponse(url="/my-cards", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"is_logged_in": False},
    )


@app.get("/version")
async def version():
    """Plain-text short git SHA the running process started with. Used by
    the deploy script to verify systemctl actually picked up the new code
    after restart (the value is computed at process start in
    app/core/templates._compute_asset_version)."""
    return {"version": ASSET_VERSION}


app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(customer.router, tags=["customer"])
app.include_router(shops.router, prefix="/shop", tags=["shops"])
app.include_router(issuance.router, prefix="/shop", tags=["issuance"])
app.include_router(branches.router, prefix="/shop/branches", tags=["branches"])
app.include_router(team.router, prefix="/shop/team", tags=["team"])
app.include_router(deereach.router, prefix="/shop/deereach", tags=["deereach"])
