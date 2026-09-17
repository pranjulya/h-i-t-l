"""Readiness integration tests against a real PostgreSQL 16.

These tests require PostgreSQL via TEST_DATABASE_URL and fail (not skip) when
it is unreachable: readiness truthfulness is a release gate.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.api.app import create_app
from hitl_ops.infrastructure.database import (
    MigrationHeadMismatch,
    verify_migration_head,
)
from tests.conftest import make_settings


def test_ready_with_clean_postgres(requires_postgres: AsyncEngine) -> None:
    del requires_postgres
    with TestClient(create_app(make_settings())) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_fails_closed_on_migration_mismatch(
    requires_postgres: AsyncEngine, monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    del requires_postgres
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
