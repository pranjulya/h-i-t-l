"""Intent route API tests."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from hitl_ops.api.app import create_app
from tests.api.conftest import api_settings, bearer
from tests.integration.conftest import reset_schema, run_alembic_upgrade

_SCALE_BODY = {
    "tool": "scale_service",
    "parameters": {"environment": "staging", "service": "api", "replicas": 4},
    "rationale": "load test",
    "source": "DIRECT",
}


def _prepare() -> None:
    settings = api_settings()
    reset_schema(settings.database_url)
    run_alembic_upgrade(settings.database_url, "head")


def test_create_intent_returns_routed_snapshot() -> None:
    _prepare()
    with TestClient(create_app(api_settings())) as client:
        response = client.post(
            "/v1/intents", json=_SCALE_BODY, headers={**bearer(), "Idempotency-Key": "i-1"}
        )
    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "AUTO_APPROVED"
    assert body["risk"]["band"] == "MEDIUM"
    assert len(body["digest"]) == 64
    assert body["links"]["self"].startswith("/v1/intents/")


def test_mutations_require_idempotency_key() -> None:
    _prepare()
    with TestClient(create_app(api_settings())) as client:
        response = client.post("/v1/intents", json=_SCALE_BODY, headers=bearer())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_unknown_tool_is_rejected_with_stable_code() -> None:
    _prepare()
    with TestClient(create_app(api_settings())) as client:
        response = client.post(
            "/v1/intents",
            json={"tool": "deploy_everything", "parameters": {}},
            headers={**bearer(), "Idempotency-Key": "i-2"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNKNOWN_TOOL"


def test_extra_parameters_are_rejected() -> None:
    _prepare()
    body = {**_SCALE_BODY, "parameters": {**_SCALE_BODY["parameters"], "force": True}}
    with TestClient(create_app(api_settings())) as client:
        response = client.post(
            "/v1/intents", json=body, headers={**bearer(), "Idempotency-Key": "i-3"}
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_idempotent_replay_returns_original_response() -> None:
    _prepare()
    with TestClient(create_app(api_settings())) as client:
        first = client.post(
            "/v1/intents", json=_SCALE_BODY, headers={**bearer(), "Idempotency-Key": "i-4"}
        )
        replay = client.post(
            "/v1/intents", json=_SCALE_BODY, headers={**bearer(), "Idempotency-Key": "i-4"}
        )
        conflict = client.post(
            "/v1/intents",
            json={**_SCALE_BODY, "parameters": {**_SCALE_BODY["parameters"], "replicas": 5}},
            headers={**bearer(), "Idempotency-Key": "i-4"},
        )
    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["intent_id"] == first.json()["intent_id"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_get_intent_and_cross_tenant_isolation() -> None:
    _prepare()
    with TestClient(create_app(api_settings())) as client:
        created = client.post(
            "/v1/intents", json=_SCALE_BODY, headers={**bearer(), "Idempotency-Key": "i-5"}
        ).json()
        got = client.get(f"/v1/intents/{created['intent_id']}", headers=bearer())
        foreign = client.get(
            f"/v1/intents/{created['intent_id']}", headers=bearer(actor="user-9", tenant="tenant-2")
        )
        missing = client.get(f"/v1/intents/{uuid.uuid4()}", headers=bearer())
    assert got.status_code == 200
    assert got.json()["state"] == "AUTO_APPROVED"
    assert got.json()["policy"]["disposition"] == "ALLOW"
    assert foreign.status_code == 404
    assert missing.status_code == 404


def test_events_are_ordered_with_cursor_pagination() -> None:
    _prepare()
    with TestClient(create_app(api_settings())) as client:
        created = client.post(
            "/v1/intents",
            json={
                "tool": "restart_service",
                "parameters": {
                    "environment": "staging",
                    "service": "api",
                    "strategy": "rolling",
                },
            },
            headers={**bearer(), "Idempotency-Key": "i-6"},
        ).json()
        page1 = client.get(
            f"/v1/intents/{created['intent_id']}/events",
            params={"limit": 2},
            headers=bearer(),
        ).json()
        page2 = client.get(
            f"/v1/intents/{created['intent_id']}/events",
            params={"limit": 10, "cursor": page1["next_cursor"]},
            headers=bearer(),
        ).json()
    sequences = [e["sequence"] for e in page1["events"]]
    assert sequences == [1, 2]
    assert [e["sequence"] for e in page2["events"]] == [3, 4]


def test_there_is_no_public_execute_route() -> None:
    _prepare()
    with TestClient(create_app(api_settings())) as client:
        response = client.post(
            f"/v1/intents/{uuid.uuid4()}/execute", headers={**bearer(), "Idempotency-Key": "i-7"}
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_requests_without_token_are_rejected() -> None:
    _prepare()
    with TestClient(create_app(api_settings())) as client:
        response = client.get(f"/v1/intents/{uuid.uuid4()}")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
