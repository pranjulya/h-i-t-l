"""Database outage and fail-closed behavior tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from hitl_ops.api.app import create_app
from hitl_ops.infrastructure.orm import ActionIntentORM
from tests.api.conftest import api_settings, bearer

_DEAD_URL = "postgresql+asyncpg://postgres@localhost:1/hitl_ops"


def test_readiness_fails_closed_when_database_is_lost() -> None:
    with TestClient(create_app(api_settings(database_url=_DEAD_URL))) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
    assert live.status_code == 200
    assert ready.status_code == 503
    assert ready.json() == {"status": "unavailable"}


def test_mutations_fail_closed_without_database() -> None:
    with TestClient(
        create_app(api_settings(database_url=_DEAD_URL)), raise_server_exceptions=False
    ) as client:
        response = client.post(
            "/v1/intents",
            json={
                "tool": "scale_service",
                "parameters": {"environment": "staging", "service": "api", "replicas": 1},
            },
            headers={**bearer(), "Idempotency-Key": "db-out-1"},
        )
    assert response.status_code in (500, 503)
    error = response.json()["error"]
    assert error["retryable"] is True
    assert error["code"] in ("INTERNAL_ERROR", "STATE_CONFLICT")
    assert "localhost" not in error["message"]


def test_no_mutation_occurs_during_database_uncertainty() -> None:
    """A failed create must leave no partial evidence once the DB returns."""

    from hitl_ops.infrastructure.database import build_engine, build_sessionmaker
    from tests.integration.conftest import reset_schema, run_alembic_upgrade

    settings = api_settings()
    reset_schema(settings.database_url)
    run_alembic_upgrade(settings.database_url, "head")
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/v1/intents",
            json={
                "tool": "scale_service",
                "parameters": {"environment": "staging", "service": "api", "replicas": 1},
            },
            headers={**bearer(), "Idempotency-Key": "db-ok-1"},
        )
        assert created.status_code == 201

    async def count() -> int:
        engine = build_engine(settings.database_url)
        try:
            maker = build_sessionmaker(engine)
            async with maker() as session, session.begin():
                return (
                    await session.execute(select(func.count()).select_from(ActionIntentORM))
                ).scalar_one()
        finally:
            await engine.dispose()

    import asyncio

    assert asyncio.run(count()) == 1
