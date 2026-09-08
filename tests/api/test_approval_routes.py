"""Approval route API tests."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.api.app import create_app
from hitl_ops.infrastructure.database import build_engine, build_sessionmaker
from hitl_ops.infrastructure.orm import RoleAssignmentORM
from tests.api.conftest import api_settings, bearer
from tests.integration.conftest import reset_schema, run_alembic_upgrade

_RESTART_BODY = {
    "tool": "restart_service",
    "parameters": {"environment": "staging", "service": "api", "strategy": "rolling"},
}


def _prepare(engine: AsyncEngine) -> None:
    settings = api_settings()
    reset_schema(settings.database_url)
    run_alembic_upgrade(settings.database_url, "head")


def _grant_approver(engine: AsyncEngine, principal: str) -> None:
    async def grant() -> None:
        maker = build_sessionmaker(build_engine(api_settings().database_url))
        async with maker() as session, session.begin():
            session.add(
                RoleAssignmentORM(
                    tenant_id="tenant-1",
                    principal_id=principal,
                    role="approver",
                    environments=None,
                    granted_by="test",
                )
            )

    import asyncio

    asyncio.run(grant())


def _create_pending(client: TestClient, idem: str) -> dict:
    created = client.post(
        "/v1/intents", json=_RESTART_BODY, headers={**bearer(), "Idempotency-Key": idem}
    ).json()
    current = client.get(f"/v1/intents/{created['intent_id']}", headers=bearer()).json()
    return {
        "intent_id": created["intent_id"],
        "digest": created["digest"],
        **{"version": current["state_version"]},
    }


def test_approval_route_commits_decision(migrated_database, engine) -> None:
    _prepare(engine)
    _grant_approver(engine, "approver-1")
    with TestClient(create_app(api_settings())) as client:
        pending = _create_pending(client, "a-1")
        response = client.post(
            f"/v1/intents/{pending['intent_id']}/approvals",
            json={
                "revision": 1,
                "intent_digest": pending["digest"],
                "level": 1,
                "decision": "APPROVE",
                "reason": "looks safe",
                "expected_state_version": pending["version"],
            },
            headers=bearer(actor="approver-1"),
        )
    assert response.status_code == 200
    assert response.json()["state"] == "APPROVED"


def test_approval_stale_digest_conflicts(migrated_database, engine) -> None:
    _prepare(engine)
    _grant_approver(engine, "approver-1")
    with TestClient(create_app(api_settings())) as client:
        pending = _create_pending(client, "a-2")
        response = client.post(
            f"/v1/intents/{pending['intent_id']}/approvals",
            json={
                "revision": 1,
                "intent_digest": "f" * 64,
                "level": 1,
                "decision": "APPROVE",
                "reason": "stale",
                "expected_state_version": pending["version"],
            },
            headers=bearer(actor="approver-1"),
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "APPROVAL_STALE"


def test_unauthorized_approver_gets_forbidden(migrated_database, engine) -> None:
    _prepare(engine)
    with TestClient(create_app(api_settings())) as client:
        pending = _create_pending(client, "a-3")
        response = client.post(
            f"/v1/intents/{pending['intent_id']}/approvals",
            json={
                "revision": 1,
                "intent_digest": pending["digest"],
                "level": 1,
                "decision": "APPROVE",
                "reason": "no role",
                "expected_state_version": pending["version"],
            },
            headers=bearer(actor="random-1"),
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_cancel_route_by_requester(migrated_database, engine) -> None:
    _prepare(engine)
    with TestClient(create_app(api_settings())) as client:
        pending = _create_pending(client, "a-4")
        response = client.post(
            f"/v1/intents/{pending['intent_id']}/cancel",
            json={
                "revision": 1,
                "reason": "no longer needed",
                "expected_state_version": pending["version"],
            },
            headers=bearer(),
        )
    assert response.status_code == 200
    assert response.json()["state"] == "CANCELLED"


def test_missing_intent_returns_not_found(migrated_database, engine) -> None:
    _prepare(engine)
    with TestClient(create_app(api_settings())) as client:
        response = client.post(
            f"/v1/intents/{uuid.uuid4()}/approvals",
            json={
                "revision": 1,
                "intent_digest": "a" * 64,
                "level": 1,
                "decision": "APPROVE",
                "reason": "missing",
                "expected_state_version": 4,
            },
            headers=bearer(actor="approver-1"),
        )
    assert response.status_code == 404
