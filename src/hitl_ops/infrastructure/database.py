"""Async SQLAlchemy engine, session factory, and readiness primitives."""

from __future__ import annotations

import os
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class MigrationHeadMismatch(RuntimeError):
    """Database schema revision does not match the migration head."""


def build_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def check_connectivity(connection: AsyncConnection) -> None:
    await connection.execute(text("SELECT 1"))


def _alembic_ini_path(ini_path: str | None = None) -> Path | None:
    candidate = Path(ini_path or os.environ.get("ALEMBIC_INI", "alembic.ini"))
    return candidate if candidate.exists() else None


def _script_head(ini_path: str | None) -> str | None:
    path = _alembic_ini_path(ini_path)
    if path is None:
        return None
    script = ScriptDirectory.from_config(AlembicConfig(str(path)))
    return script.get_current_head()


async def verify_migration_head(connection: AsyncConnection, ini_path: str | None = None) -> None:
    """Fail closed when the database revision and migration head disagree."""

    head = _script_head(ini_path)

    def _current_revision(sync_connection: Connection) -> str | None:
        context = MigrationContext.configure(sync_connection)
        try:
            return context.get_current_revision()
        except Exception:
            return None

    db_revision = await connection.run_sync(_current_revision)
    if head != db_revision:
        raise MigrationHeadMismatch("database schema revision does not match the migration head")
