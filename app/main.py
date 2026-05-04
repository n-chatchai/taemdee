from contextlib import asynccontextmanager
from http.cookies import SimpleCookie

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.datastructures import MutableHeaders

from loguru import logger

from app.core.auth import (
    CUSTOMER_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    CustomerAuthError,
    SessionAuthError,
)
from app.core.config import settings
from app.core.database import engine, get_session
from app.core.templates import ASSET_VERSION, templates
from app.models import Customer, Shop
from app.routes import (
    auth,
    branches,
    customer,
    deereach,
    issuance,
    pairing,
    shops,
    staff_join,
    team,
)
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


# ---------------------------------------------------------------------------
# Pure ASGI middleware
#
# Starlette's @app.middleware("http") wraps every response in BaseHTTPMiddleware,
# which buffers and re-streams the body. That re-streaming has a long-standing
# bug with FileResponse + Range/disconnect: chunks can exceed the original
# Content-Length and uvicorn raises "Response content longer than
# Content-Length". We hit this in prod once /sw.js (a FileResponse fetched on
# every page load by the eager SW registration) was wrapped by three of these.
#
# Pure ASGI middleware doesn't buffer — it only inspects scope/messages and
# wraps send() when it specifically needs to mutate response.start headers.
# That sidesteps the bug entirely.
# ---------------------------------------------------------------------------


class RevalidateHTMLMiddleware:
    """Stamp Cache-Control: no-cache on text/html responses so iOS Safari PWA
    tap-to-open doesn't serve a stale cached page after a deploy. Wraps send()
    only — never touches the body — so it's safe for FileResponse / Range.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                if headers.get("content-type", "").startswith("text/html"):
                    headers["cache-control"] = "no-cache, must-revalidate"
            await send(message)

        await self.app(scope, receive, send_wrapper)


class CustomerContextMiddleware:
    """Resolve the customer cookie once per request and stash the Customer on
    scope["state"] so Jinja templates can reach it via request.state.customer.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Lazy import: avoids a top-of-module DB engine import that would
        # fire during test collection before settings are patched.
        from app.core.database import SessionFactory

        customer = None
        cookie_header = ""
        for k, v in scope.get("headers", []):
            if k == b"cookie":
                cookie_header = v.decode("latin-1")
                break
        if cookie_header:
            jar = SimpleCookie()
            jar.load(cookie_header)
            morsel = jar.get(CUSTOMER_COOKIE_NAME)
            if morsel:
                customer_id = decode_customer_token(morsel.value)
                if customer_id:
                    async with SessionFactory() as db:
                        customer = await db.get(Customer, customer_id)

        # State is the canonical request-scoped dict — c_base.html reads
        # request.state.customer which proxies to scope["state"]["customer"].
        scope.setdefault("state", {})["customer"] = customer
        await self.app(scope, receive, send)


class ShopContextMiddleware:
    """Resolve the shop session cookie once per request and stash both the
    Shop and (optionally) the StaffMember on scope["state"]. Templates read
    these via request.state.shop / request.state.staff to render owner-vs-
    staff variants of shared chrome (e.g. the s3_top avatar shows the
    staff's profile picture instead of the shop logo when logged in as
    staff).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from app.core.database import SessionFactory
        from app.models import StaffMember

        shop = None
        staff = None
        cookie_header = ""
        for k, v in scope.get("headers", []):
            if k == b"cookie":
                cookie_header = v.decode("latin-1")
                break
        if cookie_header:
            jar = SimpleCookie()
            jar.load(cookie_header)
            morsel = jar.get(SESSION_COOKIE_NAME)
            if morsel:
                payload = decode_session_token(morsel.value)
                if payload and payload.get("shop_id"):
                    from uuid import UUID
                    try:
                        shop_id = UUID(payload["shop_id"])
                    except (ValueError, TypeError):
                        shop_id = None
                    staff_id_raw = payload.get("staff_id")
                    staff_id = None
                    if staff_id_raw:
                        try:
                            staff_id = UUID(staff_id_raw)
                        except (ValueError, TypeError):
                            pass
                    if shop_id:
                        async with SessionFactory() as db:
                            shop = await db.get(Shop, shop_id)
                            if staff_id:
                                staff = await db.get(StaffMember, staff_id)

        state = scope.setdefault("state", {})
        state["shop"] = shop
        state["staff"] = staff
        await self.app(scope, receive, send)


class SubdomainRoutingMiddleware:
    """Separate shop.taemdee.com from taemdee.com.

    - shop.* domain → only /shop, /auth, /staff allowed. Root / redirects to
      /shop/dashboard. Other paths bounce to the main domain.
    - main domain → marketing + customer routes. /shop/* redirects to shop.*.
    """

    # System/static routes always allowed on both hosts. /sw.js needs to be
    # available on every host (PWA scope is per-origin) and we can't redirect
    # static files because they may be requested with cookies/credentials and
    # the PWA caches the URL.
    _SYSTEM_PATHS = frozenset(
        {
            "/manifest.json",
            "/favicon.ico",
            "/version",
            "/privacy",
            "/data-deletion",
            "/sw.js",
        }
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if path.startswith("/static") or path in self._SYSTEM_PATHS:
            await self.app(scope, receive, send)
            return

        host = ""
        for k, v in scope.get("headers", []):
            if k == b"host":
                host = v.decode("latin-1").split(":")[0]
                break
        query = scope.get("query_string", b"").decode("latin-1")
        suffix = f"?{query}" if query else ""
        # In local dev, taemdee.local is main, shop.taemdee.local is shop.
        is_shop_host = host.startswith("shop.") or host == settings.shop_domain

        redirect_url = None
        if is_shop_host:
            if path == "/":
                redirect_url = "/shop/dashboard"
            elif not (
                path.startswith("/shop")
                or path.startswith("/auth")
                or path.startswith("/staff")
            ):
                # Customer URL on shop host → bounce to main. Preserve query
                # string — old printed scan QRs encode shop.* and rely on
                # ?branch=... / ?t=... surviving the redirect.
                main_host = (
                    settings.main_domain
                    if settings.environment == "production"
                    else host.replace("shop.", "")
                )
                proto = scope.get("scheme", "http")
                redirect_url = f"{proto}://{main_host}{path}{suffix}"
        elif path.startswith("/shop"):
            shop_host = (
                settings.shop_domain
                if settings.environment == "production"
                else f"shop.{host}"
            )
            proto = scope.get("scheme", "http")
            redirect_url = f"{proto}://{shop_host}{path}{suffix}"

        if redirect_url is not None:
            response = RedirectResponse(
                url=redirect_url, status_code=status.HTTP_303_SEE_OTHER
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# Order: add_middleware adds to the OUTSIDE, so the last-added wraps everything.
# Resulting execution order on inbound: SubdomainRouting → ShopContext →
# CustomerContext → RevalidateHTML → app. Outbound reverses. Both context
# middlewares are no-ops when the matching cookie is missing, so the order
# between them doesn't matter functionally.
app.add_middleware(RevalidateHTMLMiddleware)
app.add_middleware(CustomerContextMiddleware)
app.add_middleware(ShopContextMiddleware)
app.add_middleware(SubdomainRoutingMiddleware)


@app.exception_handler(SessionAuthError)
@app.exception_handler(CustomerAuthError)
@app.exception_handler(status.HTTP_401_UNAUTHORIZED)
async def auth_error_handler(request: Request, exc: HTTPException):
    """When a session is missing/invalid, redirect to the appropriate login page.

    - SessionAuthError -> /shop/login
    - CustomerAuthError -> /customer/login
    - Plain 401 -> host/path sniffing fallback
    """
    # 1. Determine if this is a Shop or Customer error
    is_shop = False
    if isinstance(exc, SessionAuthError):
        is_shop = True
    elif isinstance(exc, CustomerAuthError):
        is_shop = False
    else:
        # Fallback for plain 401s: sniff host and path
        host = request.headers.get("host", "").split(":")[0]
        is_shop_host = host.startswith("shop.") or host == settings.shop_domain
        path = request.url.path
        is_shop = is_shop_host or path.startswith("/shop")

    # 2. Set redirect target and cookie to clear
    reason = getattr(exc, "reason", "invalid")
    if is_shop:
        login_url = f"/shop/login?reason={reason}"
        cookie_to_clear = SESSION_COOKIE_NAME
    else:
        login_url = f"/customer/login?reason={reason}"
        cookie_to_clear = CUSTOMER_COOKIE_NAME

    # 3. Handle HTML vs JSON/API responses
    accept = request.headers.get("accept", "")
    # Check for HTMX or HTML requests
    is_html = "text/html" in accept or "*/*" in accept or not accept

    if is_html:
        response = RedirectResponse(
            url=login_url, status_code=status.HTTP_303_SEE_OTHER
        )
        if cookie_to_clear:
            response.delete_cookie(cookie_to_clear, path="/")
        return response

    # For API/JSON requests, return standard 401
    from fastapi.exception_handlers import http_exception_handler

    return await http_exception_handler(request, exc)


@app.exception_handler(status.HTTP_403_FORBIDDEN)
async def permission_denied_handler(request: Request, exc: HTTPException):
    """Friendly 403 page for HTML navigations — replaces the default
    JSON {"detail": "Permission denied"} that staff would see when
    they tap a link gated by require_owner / require_permission. API/
    JSON callers still get the JSON error.
    """
    accept = request.headers.get("accept", "")
    is_html = "text/html" in accept or "*/*" in accept or not accept
    if is_html:
        return templates.TemplateResponse(
            request=request,
            name="shop/no_permission.html",
            status_code=status.HTTP_403_FORBIDDEN,
            context={"detail": exc.detail},
        )
    from fastapi.exception_handlers import http_exception_handler
    return await http_exception_handler(request, exc)


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/manifest.json")
async def manifest(request: Request):
    """Serve dynamic manifest based on subdomain."""
    host = request.headers.get("host", "").split(":")[0]
    is_shop_host = host.startswith("shop.") or host == settings.shop_domain

    filename = "manifest_shop.json" if is_shop_host else "manifest.json"

    logger.info(f"Serving {filename} for {host}")
    return FileResponse(
        f"static/{filename}",
        media_type="application/json",
    )


@app.get("/favicon.ico")
async def favicon_redirect():
    """Root-level favicon for browsers that don't respect the link tag."""
    return RedirectResponse(url="/static/taemdee-icons/taemdee-icon-32.png")


@app.get("/sw.js")
async def service_worker():
    """Serve the service worker at root so it can scope to '/'.

    A SW served from /static/js/sw.js is scoped to /static/js/ by default,
    which means it can't intercept top-level navigations. Serving the same
    file at root gives it root-scope without needing the
    Service-Worker-Allowed header dance.
    """
    return FileResponse(
        "static/js/sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/")
async def home(request: Request, db: AsyncSession = Depends(get_session)):
    """Marketing home for guests. Logged-in users get sent to their app —
    matters most for PWA installs (Add to Home Screen): the saved icon
    should open the user's dashboard, not the marketing pitch they've
    already seen. A returning customer who hits / (e.g. via PWA fallback
    or bookmark) should land on /my-cards; a shop owner on /shop/dashboard.

    Both cookies valid → customer side wins (more common entry); shop
    owners can switch by typing /shop/dashboard themselves.
    """
    from uuid import UUID

    customer_cookie = request.cookies.get(CUSTOMER_COOKIE_NAME)
    if customer_cookie:
        customer_id = decode_customer_token(customer_cookie)
        if customer_id and await db.get(Customer, customer_id):
            return RedirectResponse(
                url="/my-cards", status_code=status.HTTP_303_SEE_OTHER
            )

    shop_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if shop_cookie:
        payload = decode_session_token(shop_cookie)
        # Verify the referenced shop still exists — otherwise /shop/dashboard
        # would raise SessionAuthError → /shop/login, stranding the user
        # behind a login wall their cookie can't satisfy.
        if payload and (shop_id := payload.get("shop_id")):
            try:
                if await db.get(Shop, UUID(shop_id)):
                    return RedirectResponse(
                        url="/shop/dashboard",
                        status_code=status.HTTP_303_SEE_OTHER,
                    )
            except ValueError:
                pass  # Malformed shop_id in token — fall through to marketing.

    return templates.TemplateResponse(request=request, name="home.html")


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
app.include_router(pairing.router, tags=["pairing"])
app.include_router(customer.router, tags=["customer"])
app.include_router(shops.router, prefix="/shop", tags=["shops"])
app.include_router(issuance.router, prefix="/shop", tags=["issuance"])
app.include_router(branches.router, prefix="/shop/branches", tags=["branches"])
app.include_router(team.router, prefix="/shop/team", tags=["team"])
app.include_router(staff_join.router, tags=["staff-join"])
app.include_router(deereach.router, prefix="/shop/deereach", tags=["deereach"])
