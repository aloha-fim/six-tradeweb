"""Database engine and session lifecycle (async SQLAlchemy 2.0 + asyncpg)."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_settings = get_settings()


def _async_dsn(url: str) -> str:
    """Guarantee the async driver. Managed Postgres (Render/Heroku/Railway/Supabase)
    hands out 'postgresql://' (or legacy 'postgres://'); the async engine needs the
    asyncpg driver named explicitly. Coerce here too, so the engine is correct even
    if the value bypassed the settings validator."""
    if "+" in url.split("://", 1)[0]:
        return url
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


engine = create_async_engine(
    _async_dsn(_settings.database_url),
    echo=False,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a scoped async session."""
    async with SessionLocal() as session:
        yield session


async def init_models() -> None:
    """Create tables if they do not yet exist (dev/CI convenience)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
