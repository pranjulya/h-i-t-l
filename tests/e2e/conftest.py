"""E2E fixtures: clean environment, authenticated clients, worker, adapter."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.adapters.demo import DemoInfrastructureAdapter
from hitl_ops.api.app import create_app
from hitl_ops.infrastructure.database import build_engine, build_sessionmaker
from hitl_ops.infrastructure.orm import RoleAssignmentORM
from tests.api.conftest import API_SECRET, api_settings, bearer  # noqa: F401
from tests.integration.conftest import reset_schema, run_alembic_upgrade


def e2e_settings() -> object:
    return api_settings()


def _prepare(settings: object) -> None:
    reset_schema(settings.database_url)  # type: ignore[attr-defined]
    run_alembic_upgrade(settings.database_url, "head")  # type: ignore[attr-defined]


def _engine() -> AsyncEngine:
    return build_engine(e2e_settings().database_url)  # type: ignore[attr-defined]


def _grant(engine: AsyncEngine, principal: str, role: str) -> None:
    import asyncio

    maker = build_sessionmaker(engine)
    grant = None

    async def run() -> None:
        async with maker() as session, session.begin():
            session.add(
                RoleAssignmentORM(
                    tenant_id="tenant-1",
                    principal_id=principal,
                    role=role,
                    environments=None,
                    granted_by="e2e",
                )
            )

    grant = run
    asyncio.run(grant())


@pytest.fixture
def clean_stack():
    """A clean environment: fresh schema, fresh app, fresh demo adapter."""

    settings = e2e_settings()
    _prepare(settings)
    for principal, role in (
        ("user-1", "approver"),
        ("approver-1", "approver"),
        ("l1-user", "critical_approver_l1"),
        ("l2-user", "critical_approver_l2"),
        ("admin-1", "administrator"),
    ):
        _grant(_engine(), principal, role)
    adapter = DemoInfrastructureAdapter()
    application = create_app(settings)  # type: ignore[arg-type]
    with TestClient(application) as client:
        stack = {"app": application, "client": client, "adapter": adapter, "settings": settings}
        yield stack
