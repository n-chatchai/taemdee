from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings

connect_args = {}
if "postgresql" in settings.database_url:
    connect_args = {
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    }

engine = create_async_engine(
    settings.database_url,
    connect_args=connect_args,
)


async def get_session():
    # expire_on_commit=False keeps already-loaded objects (e.g., a Shop loaded
    # before commit) usable afterwards. Without this, accessing any attribute
    # post-commit triggers an async lazy-load that crashes during sync template
    # rendering with `MissingGreenlet`.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
