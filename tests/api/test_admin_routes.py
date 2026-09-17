"""Administrator route API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from hitl_ops.api.app import create_app
from hitl_ops.domain.policy import SEED_POLICY_BUNDLE_RULES
from hitl_ops.infrastructure.database import build_engine, build_sessionmaker
from hitl_ops.infrastructure.orm import RoleAssignmentORM
from tests.api.conftest import api_settings, bearer
from tests.integration.conftest import reset_schema, run_alembic_upgrade

_POLICY_RULES = {
    **SEED_POLICY_BUNDLE_RULES,
    "tools": {
        **SEED_POLICY_BUNDLE_RULES["tools"],
        "inspect_service": {"disposition": "BLOCK"},
    },
}


def _prepare(engine: AsyncEngine) -> None:
    settings = api_settings()
    reset_schema(settings.database_url)
    run_alembic_upgrade(settings.database_url, "head")


def _seed_admin(engine: AsyncEngine) -> None:
    async def seed() -> None:
        maker = build_sessionmaker(build_engine(api_settings().database_url))
        async with maker() as session, session.begin():
            session.add(
                RoleAssignmentORM(
                    tenant_id="tenant-1",
                    principal_id="admin-1",
                    role="administrator",
                    environments=None,
                    granted_by="test",
                )
            )

    import asyncio

    asyncio.run(seed())


async def _grant_admin_direct(session: AsyncSession) -> None:
    session.add(
        RoleAssignmentORM(
            tenant_id="tenant-1",
            principal_id="admin-1",
            role="administrator",
            environments=None,
            granted_by="test",
        )
    )


def test_non_administrator_cannot_grant_roles(migrated_database, engine) -> None:
    _prepare(engine)
    with TestClient(create_app(api_settings())) as client:
        response = client.post(
            "/v1/admin/role-assignments",
            json={
                "principal_id": "someone",
                "role": "approver",
                "reason": "self escalation",
            },
            headers={**bearer(actor="normal-1"), "Idempotency-Key": "adm-n1"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_administrator_grants_role_and_audits(migrated_database, engine) -> None:
    _prepare(engine)
    _seed_admin(engine)
    with TestClient(create_app(api_settings())) as client:
        response = client.post(
            "/v1/admin/role-assignments",
            json={
                "principal_id": "approver-1",
                "role": "approver",
                "environments": ["staging"],
                "reason": "new joiner",
            },
            headers={**bearer(actor="admin-1"), "Idempotency-Key": "adm-g1"},
        )
    assert response.status_code == 201
    assert response.json()["role"] == "approver"


def test_admin_role_grant_replays_idempotently(migrated_database, engine) -> None:
    _prepare(engine)
    _seed_admin(engine)
    with TestClient(create_app(api_settings())) as client:
        first = client.post(
            "/v1/admin/role-assignments",
            json={
                "principal_id": "approver-1",
                "role": "approver",
                "environments": ["staging"],
                "reason": "new joiner",
            },
            headers={**bearer(actor="admin-1"), "Idempotency-Key": "adm-replay"},
        )
        replay = client.post(
            "/v1/admin/role-assignments",
            json={
                "principal_id": "approver-1",
                "role": "approver",
                "environments": ["staging"],
                "reason": "new joiner",
            },
            headers={**bearer(actor="admin-1"), "Idempotency-Key": "adm-replay"},
        )
        conflict = client.post(
            "/v1/admin/role-assignments",
            json={
                "principal_id": "approver-1",
                "role": "approver",
                "environments": ["production"],
                "reason": "new joiner",
            },
            headers={**bearer(actor="admin-1"), "Idempotency-Key": "adm-replay"},
        )
    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["assignment_id"] == first.json()["assignment_id"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_policy_bundle_lifecycle_via_routes(migrated_database, engine) -> None:
    _prepare(engine)
    _seed_admin(engine)
    with TestClient(create_app(api_settings())) as client:
        registered = client.post(
            "/v1/admin/policy-bundles",
            json={"version": "policy-2", "rules": _POLICY_RULES, "reason": "stricter"},
            headers={**bearer(actor="admin-1"), "Idempotency-Key": "adm-p1"},
        )
        activated = client.post(
            "/v1/admin/policy-bundles/policy-2/activate",
            json={"reason": "go live"},
            headers={**bearer(actor="admin-1"), "Idempotency-Key": "adm-p2"},
        )
        duplicate = client.post(
            "/v1/admin/policy-bundles",
            json={"version": "policy-2", "rules": _POLICY_RULES, "reason": "dup"},
            headers={**bearer(actor="admin-1"), "Idempotency-Key": "adm-p3"},
        )
        missing = client.post(
            "/v1/admin/policy-bundles/policy-x/activate",
            json={"reason": "typo"},
            headers={**bearer(actor="admin-1"), "Idempotency-Key": "adm-p4"},
        )
    assert registered.status_code == 201
    assert activated.status_code == 200
    assert activated.json()["active"] is True
    assert duplicate.status_code == 409
    assert missing.status_code == 404
