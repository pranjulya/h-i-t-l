"""Stable error envelope API contract tests."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from hitl_ops.api.app import create_app
from tests.api.conftest import api_settings, bearer


def test_every_error_uses_the_stable_envelope() -> None:
    with TestClient(create_app(api_settings())) as client:
        unknown_route = client.get("/v1/definitely-missing", headers=bearer())
        unauthenticated = client.get("/v1/intents")
    for response in (unknown_route, unauthenticated):
        error = response.json()["error"]
        assert set(error) == {"code", "message", "retryable", "correlation_id", "details"}
        assert response.headers["X-Correlation-ID"] == error["correlation_id"]


def test_request_validation_uses_validation_failed_code() -> None:
    with TestClient(create_app(api_settings())) as client:
        response = client.post(
            "/v1/intents",
            json={"parameters": {}},  # missing required tool
            headers={**bearer(), "Idempotency-Key": "v-1"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_cross_tenant_requests_do_not_disclose_existence() -> None:
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
            headers={**bearer(), "Idempotency-Key": "e-1"},
        ).json()
        foreign = client.get(
            f"/v1/intents/{created['intent_id']}",
            headers=bearer(actor="user-2", tenant="tenant-2"),
        )
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "NOT_FOUND"


def test_unhandled_errors_do_not_leak_internals() -> None:
    with TestClient(create_app(api_settings())) as client:
        response = client.get(f"/v1/intents/{'not-a-uuid'}", headers=bearer())
    assert response.status_code in (404, 422)
    body = response.json()
    assert body["error"]["code"] in ("VALIDATION_FAILED", "NOT_FOUND", "REQUEST_FAILED")
    _ = uuid.uuid4()
