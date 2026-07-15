from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import Base

_engine = None
async_session_factory: async_sessionmaker[AsyncSession] | None = None


def _ensure_engine() -> None:
    global _engine, async_session_factory
    if _engine is not None:
        return
    settings = get_settings()
    _engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(
        _engine, expire_on_commit=False, class_=AsyncSession
    )


async def init_db() -> None:
    _ensure_engine()
    assert _engine is not None
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    _ensure_engine()
    assert async_session_factory is not None
    async with async_session_factory() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    _ensure_engine()
    assert async_session_factory is not None
    return async_session_factory
