from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


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
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
