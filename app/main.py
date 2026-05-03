from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import CUSTOMER_COOKIE_NAME, SESSION_COOKIE_NAME, SessionAuthError
from app.core.config import settings
from app.core.database import engine, get_session
from app.core.templates import ASSET_VERSION, templates
from app.models import Customer, Shop
from app.routes import auth, branches, customer, deereach, issuance, shops, staff_join, team
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


@app.middleware("http")
async def subdomain_routing(request: Request, call_next):
    """Separate shop.taemdee.com from taemdee.com.

    - shop.* domain → only /shop, /auth, /static allowed. Root / redirects to /shop/dashboard.
    - main domain → marketing + customer routes. /shop/* redirects to shop.* domain.
    """
    host = request.headers.get("host", "").split(":")[0]
    path = request.url.path

    # System/static routes always allowed on both
    if path.startswith("/static") or path in ("/manifest.json", "/favicon.ico", "/version", "/privacy", "/data-deletion"):
        return await call_next(request)

    # In local dev, taemdee.local is the main domain, shop.taemdee.local is the shop domain.
    # We detect "shop." prefix or match settings exactly.
    is_shop_host = host.startswith("shop.") or host == settings.shop_domain
    
    if is_shop_host:
        if path == "/":
            return RedirectResponse(url="/shop/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        # Auth routes (like /auth/otp/verify) are shared but usually hit from the shop side
        if not (path.startswith("/shop") or path.startswith("/auth") or path.startswith("/staff")):
            # Customer trying to access /my-cards on shop domain? Bounce to main.
            main_host = settings.main_domain if settings.environment == "production" else host.replace("shop.", "")
            return RedirectResponse(url=f"https://{main_host}{path}", status_code=status.HTTP_303_SEE_OTHER)
    else:
        # On main domain, bounce any /shop/* requests to the shop subdomain
        if path.startswith("/shop"):
            shop_host = settings.shop_domain if settings.environment == "production" else f"shop.{host}"
            return RedirectResponse(url=f"https://{shop_host}{path}", status_code=status.HTTP_303_SEE_OTHER)

    return await call_next(request)


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


@app.get("/manifest.json")
async def manifest(request: Request):
    """Serve dynamic manifest based on subdomain."""
    host = request.headers.get("host", "").split(":")[0]
    is_shop_host = host.startswith("shop.") or host == settings.shop_domain
    
    filename = "manifest_shop.json" if is_shop_host else "manifest.json"
    return RedirectResponse(url=f"/static/{filename}")


@app.get("/favicon.ico")
async def favicon_redirect():
    """Root-level favicon for browsers that don't respect the link tag."""
    return RedirectResponse(url="/static/taemdee-icons/taemdee-icon-32.png")


@app.get("/")
async def home(request: Request, db: AsyncSession = Depends(get_session)):
    """Marketing home for guests. Logged-in users get sent to their app —
    matters most for PWA installs (Add to Home Screen): the saved icon should
    open the user's dashboard, not the marketing pitch they've already seen.

    Both cookies valid → render the role picker so the user (e.g. a shop
    owner who also collects points elsewhere) can pick which side to enter
    instead of always being slammed into /shop/dashboard.
    """
    from uuid import UUID

    valid_shop = False
    shop_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if shop_cookie:
        payload = decode_session_token(shop_cookie)
        # Verify the referenced shop still exists — otherwise /shop/dashboard
        # would raise SessionAuthError → /shop/login, stranding an anonymous
        # customer behind a login wall they don't need.
        if payload and (shop_id := payload.get("shop_id")):
            try:
                if await db.get(Shop, UUID(shop_id)):
                    valid_shop = True
            except ValueError:
                pass  # Malformed shop_id in token — treat as no shop session.

    valid_customer = False
    customer_cookie = request.cookies.get(CUSTOMER_COOKIE_NAME)
    if customer_cookie:
        customer_id = decode_customer_token(customer_cookie)
        if customer_id and await db.get(Customer, customer_id):
            valid_customer = True

    is_logged_in = valid_shop or valid_customer

    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "is_logged_in": is_logged_in,
            "valid_shop": valid_shop,
            "valid_customer": valid_customer,
        },
    )


@app.get("/privacy", tags=["pages"])
async def privacy(request: Request):
    return templates.TemplateResponse(request=request, name="privacy.html")


@app.get("/data-deletion", tags=["pages"])
async def data_deletion(request: Request):
    """Data deletion request page - required by Facebook Login."""
    return templates.TemplateResponse(request=request, name="privacy.html")


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
app.include_router(staff_join.router, tags=["staff-join"])
app.include_router(deereach.router, prefix="/shop/deereach", tags=["deereach"])
