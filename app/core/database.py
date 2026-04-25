from uuid import uuid4
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    connect_args={
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
    },
)


async def get_session():
    # expire_on_commit=False keeps already-loaded objects (e.g., a Shop loaded
    # before commit) usable afterwards. Without this, accessing any attribute
    # post-commit triggers an async lazy-load that crashes during sync template
    # rendering with `MissingGreenlet`.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
