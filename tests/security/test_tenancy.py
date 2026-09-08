"""Tenant-isolation security tests."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from hitl_ops.api.app import create_app
from tests.api.conftest import api_settings, bearer
from tests.security.conftest import hardened_database  # noqa: F401

_SCALE = {
    "tool": "scale_service",
    "parameters": {"environment": "staging", "service": "api", "replicas": 1},
}


def test_cross_tenant_enumeration_returns_identical_not_found(hardened_database) -> None:
    with TestClient(create_app(api_settings())) as client:
        created = client.post(
            "/v1/intents", json=_SCALE, headers={**bearer(), "Idempotency-Key": "t-1"}
        ).json()
        existing_missing = client.get(
            f"/v1/intents/{uuid.uuid4()}", headers=bearer(actor="user-2", tenant="tenant-2")
        )
        foreign = client.get(
            f"/v1/intents/{created['intent_id']}", headers=bearer(actor="user-2", tenant="tenant-2")
        )
    assert foreign.status_code == existing_missing.status_code == 404
    assert foreign.json()["error"]["code"] == existing_missing.json()["error"]["code"]


def test_idempotency_keys_are_scoped_per_tenant_and_actor(hardened_database) -> None:
    with TestClient(create_app(api_settings())) as client:
        tenant1 = client.post(
            "/v1/intents", json=_SCALE, headers={**bearer(), "Idempotency-Key": "shared"}
        ).json()
        tenant2 = client.post(
            "/v1/intents",
            json=_SCALE,
            headers={**bearer(actor="user-1", tenant="tenant-2"), "Idempotency-Key": "shared"},
        ).json()
    assert tenant1["intent_id"] != tenant2["intent_id"]


def test_actor_cannot_be_spoofed_via_body() -> None:
    with TestClient(create_app(api_settings())) as client:
        response = client.post(
            "/v1/intents",
            json={**_SCALE, "requester_id": "someone-else", "tenant_id": "tenant-9"},
            headers={**bearer(), "Idempotency-Key": "spoof-1"},
        )
        fetched = client.get(f"/v1/intents/{response.json()['intent_id']}", headers=bearer())
    assert response.status_code == 201
    assert fetched.json()["state"] in ("AUTO_APPROVED", "PENDING_APPROVAL_1")
