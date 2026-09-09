"""End-to-end journeys for all five tools and all approval routes.

Black-box: every step goes through the public API; the worker drives claims,
execution, and reconciliation; role seeding is the only direct database use.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hitl_ops.api.app import create_app
from hitl_ops.infrastructure.database import build_engine, build_sessionmaker
from hitl_ops.worker import run_worker_tick
from tests.api.conftest import bearer
from tests.e2e.conftest import e2e_settings

_IDEM = {"Idempotency-Key": "e2e-key"}


def _body(tool: str, parameters: dict) -> dict:
    return {"tool": tool, "parameters": parameters, "rationale": "e2e journey"}


def _approve(
    client: TestClient, intent_id: str, digest: str, version: int, actor: str, level: int = 1
):
    return client.post(
        f"/v1/intents/{intent_id}/approvals",
        json={
            "revision": 1,
            "intent_digest": digest,
            "level": level,
            "decision": "APPROVE",
            "reason": "e2e approval",
            "expected_state_version": version,
            "obligations": {
                "announce_in_incident_channel": "inc-123",
                "require_change_ticket": "CHG-123",
            },
        },
        headers={**bearer(actor=actor), "Idempotency-Key": f"approve-{intent_id}-{level}-{actor}"},
    )


def _worker(stack: dict) -> dict:
    import asyncio

    async def run() -> dict:
        engine = build_engine(stack["settings"].database_url)  # type: ignore[attr-defined]
        try:
            maker = build_sessionmaker(engine)
            return await run_worker_tick(maker, stack["adapter"])
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _state(client: TestClient, intent_id: str) -> dict:
    return client.get(f"/v1/intents/{intent_id}", headers=bearer()).json()


def test_low_inspection_auto_allows_and_executes(clean_stack) -> None:
    client = clean_stack["client"]
    created = client.post(
        "/v1/intents",
        json=_body("inspect_service", {"environment": "staging", "service": "api"}),
        headers={**bearer(), **_IDEM},
    ).json()
    assert created["state"] == "AUTO_APPROVED"
    stats = _worker(clean_stack)
    assert stats["executed"] == 1
    assert _state(client, created["intent_id"])["state"] == "SUCCEEDED"


def test_medium_policy_branches_auto_allow_then_require_approval(clean_stack) -> None:
    client = clean_stack["client"]
    staging = client.post(
        "/v1/intents",
        json=_body("scale_service", {"environment": "staging", "service": "api", "replicas": 4}),
        headers={**bearer(), **_IDEM},
    ).json()
    assert staging["state"] == "AUTO_APPROVED"

    production = client.post(
        "/v1/intents",
        json=_body("scale_service", {"environment": "production", "service": "api", "replicas": 4}),
        headers={**bearer(), "Idempotency-Key": "e2e-prod"},
    ).json()
    assert production["state"] == "PENDING_APPROVAL_1"
    assert production["risk"]["band"] == "HIGH"


def test_high_restart_requires_one_approval(clean_stack) -> None:
    client = clean_stack["client"]
    created = client.post(
        "/v1/intents",
        json=_body(
            "restart_service", {"environment": "staging", "service": "api", "strategy": "rolling"}
        ),
        headers={**bearer(), **_IDEM},
    ).json()
    current = _state(client, created["intent_id"])
    response = _approve(
        client, created["intent_id"], created["digest"], current["state_version"], "approver-1"
    )
    assert response.status_code == 200
    assert response.json()["state"] == "APPROVED"


def test_critical_deletion_requires_two_distinct_approvers(clean_stack) -> None:
    client = clean_stack["client"]
    created = client.post(
        "/v1/intents",
        json=_body(
            "delete_resource",
            {
                "environment": "production",
                "resource_type": "postgres_instance",
                "resource_id": "pg-main-1",
                "deletion_mode": "hard",
            },
        ),
        headers={**bearer(), **_IDEM},
    ).json()
    current = _state(client, created["intent_id"])

    l1 = _approve(
        client,
        created["intent_id"],
        created["digest"],
        current["state_version"],
        "l1-user",
        level=1,
    )
    assert l1.status_code == 200
    after_l1 = _state(client, created["intent_id"])
    assert after_l1["state"] == "PENDING_APPROVAL_2"

    same_actor_l2 = _approve(
        client,
        created["intent_id"],
        created["digest"],
        after_l1["state_version"],
        "l1-user",
        level=2,
    )
    assert same_actor_l2.status_code == 403

    l2 = _approve(
        client,
        created["intent_id"],
        created["digest"],
        after_l1["state_version"],
        "l2-user",
        level=2,
    )
    assert l2.status_code == 200
    assert l2.json()["state"] == "APPROVED"


def test_rejection_and_cancellation_routes(clean_stack) -> None:
    client = clean_stack["client"]
    rejected = client.post(
        "/v1/intents",
        json=_body(
            "restart_service", {"environment": "staging", "service": "api", "strategy": "rolling"}
        ),
        headers={**bearer(), "Idempotency-Key": "e2e-reject"},
    ).json()
    current = _state(client, rejected["intent_id"])
    rejection = client.post(
        f"/v1/intents/{rejected['intent_id']}/approvals",
        json={
            "revision": 1,
            "intent_digest": rejected["digest"],
            "level": 1,
            "decision": "REJECT",
            "reason": "not needed",
            "expected_state_version": current["state_version"],
        },
        headers={**bearer(actor="approver-1"), "Idempotency-Key": "e2e-reject-decide"},
    )
    assert rejection.json()["state"] == "REJECTED"

    cancelled = client.post(
        "/v1/intents",
        json=_body(
            "restart_service", {"environment": "staging", "service": "api", "strategy": "rolling"}
        ),
        headers={**bearer(), "Idempotency-Key": "e2e-cancel"},
    ).json()
    current = _state(client, cancelled["intent_id"])
    cancellation = client.post(
        f"/v1/intents/{cancelled['intent_id']}/cancel",
        json={
            "revision": 1,
            "reason": "changed mind",
            "expected_state_version": current["state_version"],
        },
        headers={**bearer(), "Idempotency-Key": "e2e-cancel-decide"},
    )
    assert cancellation.json()["state"] == "CANCELLED"


def test_provisioning_journey_end_to_end(clean_stack) -> None:
    client = clean_stack["client"]
    created = client.post(
        "/v1/intents",
        json=_body(
            "provision_resource",
            {
                "environment": "staging",
                "resource_type": "redis_cache",
                "name": "cache-e2e",
                "region": "us-east-1",
                "size": "small",
                "cost_class": "low",
            },
        ),
        headers={**bearer(), **_IDEM},
    ).json()
    current = _state(client, created["intent_id"])
    approval = _approve(
        client, created["intent_id"], created["digest"], current["state_version"], "approver-1"
    )
    assert approval.json()["state"] == "APPROVED"

    stats = _worker(clean_stack)
    assert stats["executed"] == 1
    assert _state(client, created["intent_id"])["state"] == "SUCCEEDED"


def test_duplicate_request_replays_original(clean_stack) -> None:
    client = clean_stack["client"]
    first = client.post(
        "/v1/intents",
        json=_body("scale_service", {"environment": "staging", "service": "api", "replicas": 4}),
        headers={**bearer(), **_IDEM},
    )
    replay = client.post(
        "/v1/intents",
        json=_body("scale_service", {"environment": "staging", "service": "api", "replicas": 4}),
        headers={**bearer(), **_IDEM},
    )
    assert replay.json()["intent_id"] == first.json()["intent_id"]
    assert replay.json()["digest"] == first.json()["digest"]


def test_unknown_outcome_reconciles_from_provider_evidence(clean_stack) -> None:
    client = clean_stack["client"]
    created = client.post(
        "/v1/intents",
        json=_body(
            "restart_service",
            {"environment": "staging", "service": "timeout-api", "strategy": "rolling"},
        ),
        headers={**bearer(), "Idempotency-Key": "e2e-unknown"},
    ).json()
    current = _state(client, created["intent_id"])
    _approve(
        client, created["intent_id"], created["digest"], current["state_version"], "approver-1"
    )

    stats = _worker(clean_stack)
    assert stats["executed"] == 1
    assert stats["reconciled"] == 0  # reconciliation is a separate pass
    assert _state(client, created["intent_id"])["state"] == "EXECUTION_UNKNOWN"

    second = _worker(clean_stack)
    assert second["reconciled"] == 1
    assert _state(client, created["intent_id"])["state"] == "SUCCEEDED"


def test_audit_chain_reconstructs_the_journey(clean_stack) -> None:
    client = clean_stack["client"]
    created = client.post(
        "/v1/intents",
        json=_body(
            "restart_service", {"environment": "staging", "service": "api", "strategy": "rolling"}
        ),
        headers={**bearer(), "Idempotency-Key": "e2e-audit"},
    ).json()
    current = _state(client, created["intent_id"])
    _approve(
        client, created["intent_id"], created["digest"], current["state_version"], "approver-1"
    )
    _worker(clean_stack)

    events = client.get(
        f"/v1/intents/{created['intent_id']}/events", params={"limit": 100}, headers=bearer()
    ).json()
    assert events["chain_valid"] is True
    types = [event["event_type"] for event in events["audit"]]
    assert types[0] == "intent_created"
    assert "state_transitioned" in types
    hashes = [event["event_hash"] for event in events["audit"]]
    assert len(hashes) == len(set(hashes))


def test_restart_durability_after_execution(clean_stack) -> None:
    client = clean_stack["client"]
    created = client.post(
        "/v1/intents",
        json=_body("scale_service", {"environment": "staging", "service": "api", "replicas": 3}),
        headers={**bearer(), **_IDEM},
    ).json()
    _worker(clean_stack)
    before = _state(client, created["intent_id"])
    assert before["state"] == "SUCCEEDED"

    # A "restart": brand-new application instance over the same database.
    fresh_app = create_app(e2e_settings())  # type: ignore[attr-defined]
    with TestClient(fresh_app) as fresh_client:
        after = fresh_client.get(f"/v1/intents/{created['intent_id']}", headers=bearer()).json()
    assert after["state"] == "SUCCEEDED"
    assert after["digest"] == before["digest"]
