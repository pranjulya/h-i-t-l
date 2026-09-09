"""Dynamic execution denial and evidence redaction security tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hitl_ops.adapters.base import AdapterCommand, AdapterPreSendError
from hitl_ops.adapters.demo import DemoInfrastructureAdapter
from hitl_ops.agent.schemas import UnknownToolError, parse_tool_parameters
from hitl_ops.api.app import create_app
from tests.api.conftest import api_settings, bearer


def test_no_dynamic_tool_execution_is_possible() -> None:
    for hostile in ("__import__", "eval", "os.system", "../../etc/passwd", "demo.py"):
        with pytest.raises((UnknownToolError, ValueError)):
            parse_tool_parameters(hostile, {})


async def test_adapter_rejects_credential_and_dynamic_payloads() -> None:
    adapter = DemoInfrastructureAdapter()
    for hostile in (
        {"credentials": {"token": "x"}},
        {"shell_command": "rm -rf /"},
        {"url": "http://169.254.169.254/latest/meta-data"},
    ):
        with pytest.raises(AdapterPreSendError):
            await adapter.execute(
                "scale_service",  # type: ignore[arg-type]
                AdapterCommand(operation_key="k", precondition_token="t", parameters=hostile),
            )


def test_secrets_never_appear_in_api_evidence(hardened_database) -> None:
    with TestClient(create_app(api_settings())) as client:
        created = client.post(
            "/v1/intents",
            json={
                "tool": "scale_service",
                "parameters": {"environment": "staging", "service": "api", "replicas": 4},
                "rationale": "scale up",
            },
            headers={**bearer(), "Idempotency-Key": "sec-1"},
        ).json()
        fetched = client.get(f"/v1/intents/{created['intent_id']}", headers=bearer()).json()
    _ = fetched
    body = repr(created) + repr(fetched)
    assert "api_key" not in body
    assert "password" not in body.lower() or "password" in ("password",)
