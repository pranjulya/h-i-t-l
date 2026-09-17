"""Readiness integration tests against a real PostgreSQL 16."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.api.app import create_app
from hitl_ops.infrastructure.database import (
    MigrationHeadMismatch,
    build_engine,
    verify_migration_head,
)
from tests.conftest import DEFAULT_TEST_DATABASE_URL, make_settings


def _database_available() -> bool:
    async def probe() -> None:
        engine = build_engine(DEFAULT_TEST_DATABASE_URL)
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(probe())
    except Exception:
        return False
    return True


def test_ready_with_clean_postgres() -> None:
    if not _database_available():
        pytest.skip("PostgreSQL unavailable")
    with TestClient(create_app(make_settings())) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_fails_closed_on_migration_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    if not _database_available():
        pytest.skip("PostgreSQL unavailable")
    root = tmp_path  # type: ignore[operator]
    versions = root / "alembic" / "versions"
    versions.mkdir(parents=True)
    ini = root / "alembic.ini"
    ini.write_text(f"[alembic]\nscript_location = {root / 'alembic'}\n", encoding="utf-8")
    (versions / "ffff_fake_head.py").write_text(
        'revision = "fffffake"\ndown_revision = None\nbranch_labels = None\ndepends_on = None\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ALEMBIC_INI", str(ini))
    with TestClient(create_app(make_settings())) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


async def test_verify_migration_head_raises_on_mismatch(
    requires_postgres: AsyncEngine, monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    root = tmp_path  # type: ignore[operator]
    versions = root / "alembic" / "versions"
    versions.mkdir(parents=True)
    ini = root / "alembic.ini"
    ini.write_text(f"[alembic]\nscript_location = {root / 'alembic'}\n", encoding="utf-8")
    (versions / "ffff_fake_head.py").write_text(
        'revision = "fffffake"\ndown_revision = None\nbranch_labels = None\ndepends_on = None\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ALEMBIC_INI", str(ini))
    async with requires_postgres.connect() as connection:
        with pytest.raises(MigrationHeadMismatch):
            await verify_migration_head(connection)
