"""Test fixtures — in-memory SQLite per test for isolation."""

import os

# Must be set BEFORE `app` is imported (pydantic-settings reads env at import time).
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")
os.environ.setdefault("ENVIRONMENT", "test")

from typing import AsyncGenerator  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402

from app import models  # noqa: F401, E402 — registers all model tables
from app.core.auth import SESSION_COOKIE_NAME  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import get_session  # noqa: E402
from app.models import Customer, Shop  # noqa: E402
from app.services.auth import issue_session_token  # noqa: E402

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def _isolate_dev_settings(monkeypatch):
    """Strip dev-only env values from the developer's .env so tests are deterministic.

    LINE creds: tests assert "LINE not configured" by default; tests that
    need it set should re-monkeypatch.

    login_otp_simulate: when true the /auth/otp/request response carries the
    code in JSON for the C3 page autofill. Tests assert the production-shape
    `{"ok": true}` body.
    """
    monkeypatch.setattr(settings, "line_channel_id", None)
    monkeypatch.setattr(settings, "line_channel_secret", None)
    monkeypatch.setattr(settings, "google_client_id", None)
    monkeypatch.setattr(settings, "facebook_app_id", None)
    monkeypatch.setattr(settings, "login_otp_simulate", False)
    monkeypatch.setattr(settings, "phone_login_enabled", False)


@pytest.fixture
async def engine():
    # StaticPool keeps all connections sharing the same in-memory DB so the test code
    # and the route handler see the same data.
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(engine) -> AsyncGenerator[AsyncSession, None]:
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session


@pytest.fixture
async def shop(db: AsyncSession) -> Shop:
    s = Shop(name="Test Cafe", phone="0812345678", reward_threshold=10)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


@pytest.fixture
async def customer(db: AsyncSession) -> Customer:
    c = Customer(is_anonymous=True)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


@pytest.fixture
async def app_for_test(engine):
    """FastAPI app with the DB session dep overridden to use the test engine."""
    from app.main import app  # delayed import — env vars must be set first

    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_session():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
async def client(app_for_test):
    transport = ASGITransport(app=app_for_test)
    # https:// base — Secure cookies (auth session, customer cookie) are only
    # sent back on https URLs, and the production code now sets them Secure.
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


@pytest.fixture
async def named_client(client):
    """Client whose customer cookie already has a display_name set, so
    /card/{shop_id} and /my-cards render directly instead of bouncing
    through the /onboard redirect that fires for null display_names."""
    await client.post("/card/nickname", data={"name": "พี่เทส"})
    return client


@pytest.fixture
async def auth_client(client, shop):
    """Client with a session cookie for the shop owner."""
    client.cookies.set(SESSION_COOKIE_NAME, issue_session_token(shop.id))
    return client
