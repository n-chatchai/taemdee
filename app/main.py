from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.auth import SESSION_COOKIE_NAME
from app.core.database import engine
from app.routes import auth, branches, customer, deereach, issuance, shops, team
from app.services.auth import decode_session_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed by Alembic — run `alembic upgrade head` before starting the server.
    yield
    await engine.dispose()


app = FastAPI(title="TaemDee — Digital Stamp Cards", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="app/templates")


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
