"""Rate limiting is keyed by authenticated principal, not by client address."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hitl_ops.api.app import create_app
from tests.api.conftest import api_settings, bearer

_SCALE_BODY = {
    "tool": "scale_service",
    "parameters": {"environment": "staging", "service": "api", "replicas": 4},
    "rationale": "rate limit probe",
}


def test_authenticated_callers_behind_one_address_do_not_share_a_budget(
    hardened_database,
) -> None:
    # Every TestClient request arrives from the same client address, so an
    # address-keyed limiter would throttle the second caller.
    settings = api_settings(rate_limit_per_minute=1)
    with TestClient(create_app(settings)) as client:
        first = client.post(
            "/v1/intents",
            json=_SCALE_BODY,
            headers={**bearer(actor="user-1"), "Idempotency-Key": "rl-1"},
        )
        other_caller = client.post(
            "/v1/intents",
            json=_SCALE_BODY,
            headers={**bearer(actor="user-2"), "Idempotency-Key": "rl-2"},
        )
        over_budget = client.post(
            "/v1/intents",
            json=_SCALE_BODY,
            headers={**bearer(actor="user-1"), "Idempotency-Key": "rl-3"},
        )

    assert first.status_code == 201
    assert other_caller.status_code == 201
    assert over_budget.status_code == 429
    assert over_budget.json()["error"]["code"] == "RATE_LIMITED"


def test_each_tenant_gets_its_own_budget_for_the_same_actor_id(
    hardened_database,
) -> None:
    settings = api_settings(rate_limit_per_minute=1)
    with TestClient(create_app(settings)) as client:
        # tenant-2 has no active policy bundle, so the routed outcome differs;
        # what matters here is that it is not rate limited by tenant-1's usage.
        tenant_1 = client.post(
            "/v1/intents",
            json=_SCALE_BODY,
            headers={**bearer(actor="user-1", tenant="tenant-1"), "Idempotency-Key": "rl-t1"},
        )
        tenant_2 = client.post(
            "/v1/intents",
            json=_SCALE_BODY,
            headers={**bearer(actor="user-1", tenant="tenant-2"), "Idempotency-Key": "rl-t2"},
        )

    assert tenant_1.status_code == 201
    assert tenant_2.status_code != 429
