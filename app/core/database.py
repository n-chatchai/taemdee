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
    async with AsyncSession(engine) as session:
        yield session
