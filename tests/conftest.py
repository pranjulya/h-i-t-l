"""Shared fixtures: validated test settings and a real PostgreSQL engine."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.config import Environment, Settings
from hitl_ops.infrastructure.database import build_engine

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://postgres@localhost:54329/hitl_ops"

_SENSITIVE_ENV_VARS = (
    "APP_ENV",
    "DEBUG",
    "LOG_LEVEL",
    "DATABASE_URL",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": Environment.TEST,
        "database_url": os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SENSITIVE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    built = build_engine(settings.database_url)
    try:
        yield built
    finally:
        await built.dispose()


@pytest.fixture
async def requires_postgres(engine: AsyncEngine) -> AsyncEngine:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL unavailable: {type(exc).__name__}")
    return engine
