"""SQLAlchemy base configuration."""

from datetime import datetime
from typing import AsyncGenerator

from sqlalchemy import DateTime, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.config import get_settings
from backend.utils.time_utils import utc_now


settings = get_settings()

# 连接池配置 — 支持直连和 PgBouncer 两种模式
# PgBouncer (transaction mode): pool_size=5, max_overflow=5, server_side_prepared_statements=False
# 直连 PostgreSQL:               pool_size=10, max_overflow=20 (默认)
#
# 通过环境变量 DB_POOL_SIZE / DB_MAX_OVERFLOW 控制
# PgBouncer 模式下，实际服务端连接由 PgBouncer 管理，应用侧连接池可以更小

_connect_args = {
    "timeout": 30,
    "command_timeout": 120,
}

# PgBouncer transaction 模式不支持 prepared statements
if settings.db_use_pgbouncer:
    _connect_args["statement_cache_size"] = 0

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=1800,
    connect_args=_connect_args,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base class for all database models."""

    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(__import__("uuid").uuid4()))
    # timezone-aware UTC (timestamptz) — see utils/time_utils.py for the rationale
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Get database session for dependency injection."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database - create all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()
