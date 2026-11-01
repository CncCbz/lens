from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


def normalize_async_database_url(database_url: str) -> str:
    normalized = database_url.strip()
    if normalized.startswith("postgresql://"):
        normalized = normalized.replace("postgresql://", "postgresql+psycopg://", 1)
    elif normalized.startswith("postgres://"):
        normalized = normalized.replace("postgres://", "postgresql+psycopg://", 1)
    elif normalized.startswith("postgresql+asyncpg://"):
        normalized = normalized.replace(
            "postgresql+asyncpg://", "postgresql+psycopg://", 1
        )
    if not normalized.startswith("postgresql+psycopg://"):
        raise ValueError("LENS_DATABASE_URL must use PostgreSQL")
    return normalized


def normalize_sync_database_url(database_url: str) -> str:
    return normalize_async_database_url(database_url)


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        normalize_async_database_url(database_url),
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=settings.db_pool_recycle_seconds,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
