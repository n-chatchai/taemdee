from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.auth import SESSION_COOKIE_NAME, SessionAuthError
from app.core.database import engine
from app.core.templates import templates
from app.routes import auth, branches, customer, deereach, issuance, shops, team
from app.services.auth import decode_session_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed by Alembic — run `alembic upgrade head` before starting the server.
    yield
    await engine.dispose()


app = FastAPI(title="TaemDee — Digital Point Cards", lifespan=lifespan)


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
async def home(request: Request):
    session_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    is_logged_in = bool(session_cookie and decode_session_token(session_cookie))
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"is_logged_in": is_logged_in},
    )


app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(customer.router, tags=["customer"])
app.include_router(shops.router, prefix="/shop", tags=["shops"])
app.include_router(issuance.router, prefix="/shop", tags=["issuance"])
app.include_router(branches.router, prefix="/shop/branches", tags=["branches"])
app.include_router(team.router, prefix="/shop/team", tags=["team"])
app.include_router(deereach.router, prefix="/shop/deereach", tags=["deereach"])
