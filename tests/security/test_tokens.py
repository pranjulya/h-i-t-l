"""Token and role security tests through the API boundary."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.api.app import create_app
from hitl_ops.infrastructure.database import build_engine, build_sessionmaker
from hitl_ops.infrastructure.orm import RoleAssignmentORM
from tests.api.conftest import API_SECRET, api_settings, bearer
from tests.integration.conftest import reset_schema, run_alembic_upgrade

_RESTART = {
    "tool": "restart_service",
    "parameters": {"environment": "staging", "service": "api", "strategy": "rolling"},
}


def _prepare(engine: AsyncEngine) -> None:
    settings = api_settings()
    reset_schema(settings.database_url)
    run_alembic_upgrade(settings.database_url, "head")


def _grant(
    engine: AsyncEngine, principal: str, role: str, environments: list[str] | None = None
) -> None:
    async def grant() -> None:
        maker = build_sessionmaker(build_engine(api_settings().database_url))
        async with maker() as session, session.begin():
            session.add(
                RoleAssignmentORM(
                    tenant_id="tenant-1",
                    principal_id=principal,
                    role=role,
                    environments=environments,
                    granted_by="test",
                )
            )

    asyncio.run(grant())


def _token(**claims: object) -> str:
    base: dict[str, object] = {
        "sub": "user-1",
        "tenant": "tenant-1",
        "iss": "https://test-issuer.local",
        "aud": "hitl-ops",
        "exp": datetime.now(UTC).timestamp() + 600,
    }
    base.update(claims)
    return jwt.encode(base, API_SECRET, algorithm="HS256")


def _header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_forged_token_is_rejected(hardened_database, engine) -> None:
    _prepare(engine)
    forged = jwt.encode(
        {
            "sub": "attacker",
            "tenant": "tenant-1",
            "iss": "https://test-issuer.local",
            "aud": "hitl-ops",
            "exp": 9999999999,
        },
        "wrong-secret",
        algorithm="HS256",
    )
    with TestClient(create_app(api_settings())) as client:
        response = client.get(f"/v1/intents/{uuid.uuid4()}", headers=_header(forged))
    assert response.status_code == 403


def test_expired_token_is_rejected(hardened_database, engine) -> None:
    _prepare(engine)
    expired = _token(exp=1)
    with TestClient(create_app(api_settings())) as client:
        response = client.get(f"/v1/intents/{uuid.uuid4()}", headers=_header(expired))
    assert response.status_code == 403


def test_wrong_issuer_and_audience_are_rejected(hardened_database, engine) -> None:
    _prepare(engine)
    with TestClient(create_app(api_settings())) as client:
        issuer = client.get(
            f"/v1/intents/{uuid.uuid4()}", headers=_header(_token(iss="https://evil.example"))
        )
        audience = client.get(
            f"/v1/intents/{uuid.uuid4()}", headers=_header(_token(aud="other-service"))
        )
    assert issuer.status_code == 403
    assert audience.status_code == 403


def test_revoked_approver_cannot_decide(hardened_database, engine) -> None:
    _prepare(engine)
    _grant(engine, "approver-1", "approver")

    async def revoke() -> None:
        maker = build_sessionmaker(build_engine(api_settings().database_url))
        async with maker() as session, session.begin():
            await session.execute(
                update(RoleAssignmentORM)
                .where(RoleAssignmentORM.principal_id == "approver-1")
                .values(revoked_at=datetime.now(UTC))
            )

    asyncio.run(revoke())
    with TestClient(create_app(api_settings())) as client:
        created = client.post(
            "/v1/intents", json=_RESTART, headers={**bearer(), "Idempotency-Key": "rev-1"}
        ).json()
        current = client.get(f"/v1/intents/{created['intent_id']}", headers=bearer()).json()
        response = client.post(
            f"/v1/intents/{created['intent_id']}/approvals",
            json={
                "revision": 1,
                "intent_digest": created["digest"],
                "level": 1,
                "decision": "APPROVE",
                "reason": "revoked role attempt",
                "expected_state_version": current["state_version"],
                "obligations": {"announce_in_incident_channel": "inc-123"},
            },
            headers={**bearer(actor="approver-1"), "Idempotency-Key": "rev-d"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_environment_scoped_role_cannot_approve_other_environment(
    hardened_database, engine
) -> None:
    _prepare(engine)
    _grant(engine, "scoped-approver", "approver", environments=["production"])
    with TestClient(create_app(api_settings())) as client:
        created = client.post(
            "/v1/intents", json=_RESTART, headers={**bearer(), "Idempotency-Key": "scope-1"}
        ).json()
        current = client.get(f"/v1/intents/{created['intent_id']}", headers=bearer()).json()
        response = client.post(
            f"/v1/intents/{created['intent_id']}/approvals",
            json={
                "revision": 1,
                "intent_digest": created["digest"],
                "level": 1,
                "decision": "APPROVE",
                "reason": "wrong scope",
                "expected_state_version": current["state_version"],
                "obligations": {"announce_in_incident_channel": "inc-123"},
            },
            headers={**bearer(actor="scoped-approver"), "Idempotency-Key": "scope-d"},
        )
    assert response.status_code == 403
