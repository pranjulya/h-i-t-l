"""Integration fixtures: schema reset and migration rehearsal helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from hitl_ops.config import Settings

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def run_alembic_upgrade(database_url: str, target: str) -> None:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, target)


def run_alembic_downgrade(database_url: str, target: str) -> None:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(cfg, target)


def reset_schema(database_url: str) -> None:
    async def reset() -> None:
        from sqlalchemy import text

        from hitl_ops.infrastructure.database import build_engine

        engine = build_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("DROP SCHEMA public CASCADE"))
                await connection.execute(text("CREATE SCHEMA public"))
        finally:
            await engine.dispose()

    asyncio.run(reset())


@pytest.fixture
def migrated_database(settings: Settings) -> str:
    database_url = settings.database_url
    reset_schema(database_url)
    run_alembic_upgrade(database_url, "head")
    return database_url
