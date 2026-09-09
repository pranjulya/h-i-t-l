"""Shared adapter contract: one test suite for all five tools."""

from __future__ import annotations

import pytest

from hitl_ops.adapters.base import AdapterCommand, AdapterPreSendError
from hitl_ops.adapters.demo import DemoInfrastructureAdapter
from hitl_ops.domain.enums import ExecutionOutcome, ToolName

_CASES = {
    ToolName.INSPECT_SERVICE: {"environment": "staging", "service": "api"},
    ToolName.RESTART_SERVICE: {"environment": "staging", "service": "api", "strategy": "rolling"},
    ToolName.SCALE_SERVICE: {"environment": "staging", "service": "api", "replicas": 4},
    ToolName.PROVISION_RESOURCE: {
        "environment": "staging",
        "resource_type": "redis_cache",
        "name": "cache-1",
        "region": "us-east-1",
        "size": "small",
        "cost_class": "low",
    },
    ToolName.DELETE_RESOURCE: {
        "environment": "staging",
        "resource_type": "postgres_instance",
        "resource_id": "pg-1",
        "deletion_mode": "soft",
    },
}


def _command(tool: ToolName) -> AdapterCommand:
    return AdapterCommand(
        operation_key=f"tenant-1:intent-1:1:{tool.value}",
        precondition_token="tok-123",
        parameters=dict(_CASES[tool]),
    )


@pytest.mark.parametrize("tool", list(ToolName))
async def test_every_tool_succeeds_with_provider_evidence(tool: ToolName) -> None:
    adapter = DemoInfrastructureAdapter()
    result = await adapter.execute(tool, _command(tool))
    assert result.outcome == ExecutionOutcome.SUCCEEDED.value
    assert result.provider_operation_id
    assert result.summary["applied"] is True


@pytest.mark.parametrize("tool", list(ToolName))
async def test_duplicate_delivery_returns_identical_evidence(tool: ToolName) -> None:
    adapter = DemoInfrastructureAdapter()
    command = _command(tool)
    first = await adapter.execute(tool, command)
    second = await adapter.execute(tool, command)
    assert second.provider_operation_id == first.provider_operation_id
    assert second.summary["duplicate_delivery"] is True


async def test_timeout_after_send_is_ambiguous_not_failed() -> None:
    adapter = DemoInfrastructureAdapter()
    command = _command(ToolName.RESTART_SERVICE)
    command.parameters["service"] = "timeout-api"
    result = await adapter.execute(ToolName.RESTART_SERVICE, command)
    assert result.outcome == ExecutionOutcome.UNKNOWN.value
    assert result.provider_operation_id is not None
    # Provider-side evidence exists for reconciliation.
    status = await adapter.lookup_status(
        ToolName.RESTART_SERVICE, command.operation_key, result.provider_operation_id
    )
    assert status.outcome == ExecutionOutcome.SUCCEEDED.value


async def test_provider_failure_is_confirmed_failed() -> None:
    adapter = DemoInfrastructureAdapter()
    command = _command(ToolName.SCALE_SERVICE)
    command.parameters["service"] = "boom-api"
    result = await adapter.execute(ToolName.SCALE_SERVICE, command)
    assert result.outcome == ExecutionOutcome.FAILED.value
    assert result.error_code == "DEMO_PROVIDER_FAILURE"


async def test_missing_provider_evidence_preserves_unknown() -> None:
    adapter = DemoInfrastructureAdapter()
    status = await adapter.lookup_status(ToolName.SCALE_SERVICE, "unknown-key", None)
    assert status.outcome == ExecutionOutcome.UNKNOWN.value
    assert status.summary["evidence"] == "none"


async def test_adapter_rejects_credential_like_parameters() -> None:
    adapter = DemoInfrastructureAdapter()
    command = AdapterCommand(
        operation_key="k",
        precondition_token="t",
        parameters={"environment": "staging", "service": "api", "credentials": {"token": "x"}},
    )
    with pytest.raises(AdapterPreSendError):
        await adapter.execute(ToolName.INSPECT_SERVICE, command)


async def test_stale_resource_version_is_rejected_without_side_effect() -> None:
    adapter = DemoInfrastructureAdapter()
    tool = ToolName.SCALE_SERVICE
    parameters = dict(_CASES[tool])
    snapshot = await adapter.fetch(tool, parameters)
    assert snapshot.resource_version is not None

    # The target changes between revalidation and execution.
    adapter.rotate_target(tool, parameters)

    stale = AdapterCommand(
        operation_key="tenant-1:stale:1",
        precondition_token="tok-stale",
        parameters=parameters,
        resource_version=snapshot.resource_version,
    )
    with pytest.raises(AdapterPreSendError):
        await adapter.execute(tool, stale)
    # No side effect was recorded for the rejected mutation.
    assert "tenant-1:stale:1" not in adapter._operations

    # A fresh fetch observes the new version and the mutation proceeds.
    fresh = await adapter.fetch(tool, parameters)
    assert fresh.resource_version != snapshot.resource_version
    result = await adapter.execute(
        tool,
        AdapterCommand(
            operation_key="tenant-1:stale:1",
            precondition_token="tok-fresh",
            parameters=parameters,
            resource_version=fresh.resource_version,
        ),
    )
    assert result.outcome == ExecutionOutcome.SUCCEEDED.value
