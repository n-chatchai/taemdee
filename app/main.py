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


async def _ensure_vapid_keys(db=None) -> None:
    """Bootstrap the VAPID keypair the DeeReach Web Push channel needs.

    Order of precedence:
      1. .env vars  (operator override; nothing to do)
      2. app_secrets DB row  (already provisioned on a prior boot)
      3. fresh keypair, written to app_secrets

    Race-safe across gunicorn workers: each worker reads first, only one
    INSERT will commit, the others see the row on the second read. We
    swallow IntegrityError so the loser doesn't crash on boot.

    `db` lets the test suite inject a SQLite session; production passes
    None and we open a fresh AsyncSession on the global engine.
    """
    from app.core.config import settings as app_settings
    if app_settings.web_push_vapid_public_key and app_settings.web_push_vapid_private_key:
        return

    import base64
    import logging
    from contextlib import asynccontextmanager
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01
    from sqlalchemy.exc import IntegrityError
    from sqlmodel import select
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.models import AppSecret

    log = logging.getLogger(__name__)
    PUB_KEY_NAME = "web_push_vapid_public"
    PRIV_KEY_NAME = "web_push_vapid_private"

    @asynccontextmanager
    async def _session():
        if db is not None:
            yield db
        else:
            async with AsyncSession(engine) as s:
                yield s

    async with _session() as session:
        rows = (await session.exec(
            select(AppSecret).where(AppSecret.name.in_([PUB_KEY_NAME, PRIV_KEY_NAME]))
        )).all()
        existing = {row.name: row.value for row in rows}

        if PUB_KEY_NAME in existing and PRIV_KEY_NAME in existing:
            app_settings.web_push_vapid_public_key = existing[PUB_KEY_NAME]
            app_settings.web_push_vapid_private_key = existing[PRIV_KEY_NAME]
            return

        # Generate a fresh ECDSA P-256 keypair. Public key goes out in the
        # X.962 uncompressed form, base64url-encoded — the format browsers
        # expect for `applicationServerKey` on pushManager.subscribe().
        # Private key serialises to PEM PKCS#8 — pywebpush takes that as
        # `vapid_private_key`.
        v = Vapid01()
        v.generate_keys()
        pub_b64 = base64.urlsafe_b64encode(
            v.public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
        ).decode().rstrip("=")
        priv_pem = v.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()

        session.add(AppSecret(name=PUB_KEY_NAME, value=pub_b64))
        session.add(AppSecret(name=PRIV_KEY_NAME, value=priv_pem))
        try:
            await session.commit()
            log.info("Generated fresh VAPID keypair (web_push) — stored in app_secrets")
        except IntegrityError:
            # Another worker beat us to it. Re-read.
            await session.rollback()
            rows = (await session.exec(
                select(AppSecret).where(AppSecret.name.in_([PUB_KEY_NAME, PRIV_KEY_NAME]))
            )).all()
            existing = {row.name: row.value for row in rows}

        app_settings.web_push_vapid_public_key = existing.get(PUB_KEY_NAME, pub_b64)
        app_settings.web_push_vapid_private_key = existing.get(PRIV_KEY_NAME, priv_pem)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed by Alembic — run `alembic upgrade head` before starting the server.
    from app.services import events
    await _ensure_vapid_keys()
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
