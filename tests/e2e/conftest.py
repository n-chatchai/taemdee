import os
import subprocess
import time
import pytest
import asyncio
from sqlalchemy import create_engine
from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

# Use a physical file for E2E tests so the server process and test process share data
DB_FILE = "test_e2e.db"
DB_URL_SYNC = f"sqlite:///{DB_FILE}"
DB_URL_ASYNC = f"sqlite+aiosqlite:///{DB_FILE}"

@pytest.fixture(scope="session", autouse=True)
def setup_db():
    """Create the database and tables once per session."""
    # Ensure any old DB is removed
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    
    # Use sync engine to create tables (much easier in pytest setup)
    engine = create_engine(DB_URL_SYNC)
    from app import models  # Ensure models are registered
    SQLModel.metadata.create_all(engine)
    engine.dispose()
    
    yield
    
    # Cleanup after session
    if os.path.exists(DB_FILE):
        try:
            os.remove(DB_FILE)
        except PermissionError:
            pass # Server might still be closing

@pytest.fixture(scope="session")
def server_url():
    """Start the FastAPI app in a background process."""
    port = 8001
    host = "127.0.0.1"
    
    # Set the environment variable for the subprocess
    env = os.environ.copy()
    env["DATABASE_URL"] = DB_URL_ASYNC
    env["ENVIRONMENT"] = "test"
    env["JWT_SECRET"] = "e2e-secret"
    env["LOGIN_OTP_SIMULATE"] = "true"
    
    # Start uvicorn
    with open("server.log", "w") as log:
        proc = subprocess.Popen(
            ["uv", "run", "uvicorn", "app.main:app", "--host", host, "--port", str(port), "--log-level", "info"],
            env=env,
            stdout=log,
            stderr=log
        )
    
    # Wait for server to be ready
    time.sleep(3)
    
    yield f"http://{host}:{port}"
    
    proc.terminate()
    proc.wait()

@pytest.fixture
async def db_session():
    """Provide an async session to the E2E database for test data setup."""
    engine = create_async_engine(DB_URL_ASYNC)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()

@pytest.fixture
def sync_db():
    """Provide a synchronous SQLModel session for E2E test data setup."""
    from sqlmodel import Session, create_engine
    engine = create_engine(DB_URL_SYNC)
    with Session(engine) as session:
        yield session
    engine.dispose()

@pytest.fixture(scope="session")
def base_url(server_url):
    return server_url
