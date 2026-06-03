"""Infrastructure: async SQLAlchemy engine, session factory, and FastAPI-ready DB dependency."""

from collections.abc import AsyncGenerator

from loguru import logger
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


class DatabaseSettings(BaseSettings):
    """Loads DATABASE_URL from the environment (and optional .env file)."""

    database_url: str = Field(validation_alias="DATABASE_URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("database_url")
    @classmethod
    def require_async_postgres(cls, value: str) -> str:
        """Ensure we use the asyncpg driver URL expected by create_async_engine."""
        url = value.strip()
        if not url.startswith("postgresql+asyncpg://"):
            msg = (
                "DATABASE_URL must use the async driver "
                "(postgresql+asyncpg://user:pass@host:PORT/dbname; e.g. localhost:15432 when using docker-compose)"
            )
            raise ValueError(msg)
        return url


_settings = DatabaseSettings()

# NullPool: Celery workers call asyncio.run() multiple times per task (e.g. sync_mark_job_status
# then run_with_session). Pooled asyncpg connections stay bound to the first loop and raise
# "Future attached to a different loop" on the next run. NullPool opens/closes a connection per
# session scope on the *current* loop (fine for API + moderate worker concurrency).
engine: AsyncEngine = create_async_engine(
    _settings.database_url,
    pool_pre_ping=True,
    poolclass=NullPool,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

logger.info("Async SQLAlchemy engine configured for PostgreSQL (asyncpg)")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield one AsyncSession per request; commit on success, rollback on errors.

    Do not call ``session.begin()`` inside services: auth dependencies may already
    have started a transaction (e.g. loading the current user).
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Database session error; rolled back transaction")
            raise
