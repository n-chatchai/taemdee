from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings

connect_args = {}
engine_kwargs = {}
if "postgresql" in settings.database_url:
    connect_args = {
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    }
    # Pool sizing — defaults (5 + 10 overflow = 15) burned out in prod
    # under the SSE load: every open /sse/me or /shop/events tab holds
    # an asyncpg connection for the entire stream lifetime, and each
    # gunicorn worker adds its own pool. Bump headroom + a 30s timeout
    # so legitimate spikes fail fast instead of stacking up. The actual
    # SSE leak (Depends-yielded session held for the streaming response)
    # is fixed in routes/customer.py and routes/shops.py — this is the
    # safety net.
    #
    # Skipped for SQLite (test mode) — aiosqlite uses StaticPool which
    # rejects these kwargs.
    engine_kwargs = {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_timeout": 30,
        "pool_recycle": 1800,  # asyncpg drops idle conns after 30 min
        "pool_pre_ping": True,  # detect dead conns left over from a redeploy
    }

engine = create_async_engine(
    settings.database_url,
    connect_args=connect_args,
    **engine_kwargs,
)

# Session factory exposed for routes that need a short-lived session
# OUTSIDE FastAPI's request lifecycle (SSE endpoints, RQ workers).
# Inside a normal request, prefer `Depends(get_session)`.
SessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session():
    # expire_on_commit=False keeps already-loaded objects (e.g., a Shop loaded
    # before commit) usable afterwards. Without this, accessing any attribute
    # post-commit triggers an async lazy-load that crashes during sync template
    # rendering with `MissingGreenlet`.
    async with SessionFactory() as session:
        yield session
