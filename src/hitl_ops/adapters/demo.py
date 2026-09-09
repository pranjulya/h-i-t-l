"""Deterministic demo infrastructure adapter.

An in-memory stand-in for a real provider: idempotent by operation key,
deterministic outcomes driven by parameter values, and a provider-side status
store that makes reconciliation observable. No network, no shell, no dynamic
endpoints, no credentials.
"""

from __future__ import annotations

import hashlib
from typing import Any

from hitl_ops.adapters.base import (
    AdapterCommand,
    AdapterPreSendError,
    AdapterResult,
    TargetSnapshot,
)
from hitl_ops.domain.enums import ExecutionOutcome, ToolName

_PROVIDER_FAILURE_SERVICE = "boom-api"
_TIMEOUT_AFTER_SEND_SERVICE = "timeout-api"


def _provider_operation_id(operation_key: str) -> str:
    digest = hashlib.sha256(operation_key.encode("utf-8")).hexdigest()
    return f"prov-{digest[:12]}"


class DemoInfrastructureAdapter:
    """Owns the demo 'provider' state; idempotent duplicate delivery.

    The demo provider keeps a per-target resource version. ``fetch`` reports
    the current version; ``execute`` performs an atomic compare-and-mutate
    against the version carried in the command and raises AdapterPreSendError
    with no side effect on mismatch.
    """

    def __init__(self) -> None:
        self._operations: dict[str, dict[str, Any]] = {}
        self._target_versions: dict[str, str] = {}

    def _target_key(self, tool: ToolName, parameters: dict[str, Any]) -> str:
        identity = {
            field: parameters[field]
            for field in ("service", "name", "resource_id")
            if parameters.get(field) is not None
        }
        return f"{tool.value}:{sorted(identity.items())}"

    def rotate_target(self, tool: ToolName, parameters: dict[str, Any]) -> str:
        """Simulate an out-of-band target change; returns the new version."""

        key = self._target_key(tool, parameters)
        version = f"v{int(self._target_versions.get(key, 'v0')[1:] or 0) + 1}"
        self._target_versions[key] = version
        return version

    async def fetch(self, tool: ToolName, parameters: dict[str, Any]) -> TargetSnapshot:
        identity = {
            field: parameters[field]
            for field in ("service", "name", "resource_id")
            if parameters.get(field) is not None
        }
        key = self._target_key(tool, parameters)
        version = self._target_versions.setdefault(key, "v1")
        return TargetSnapshot(
            found=True,
            identity=identity,
            health="healthy",
            facts={},
            resource_version=version,
        )

    async def execute(self, tool: ToolName, command: AdapterCommand) -> AdapterResult:
        existing = self._operations.get(command.operation_key)
        if existing is not None:
            # Duplicate delivery: return the recorded provider evidence.
            return AdapterResult(
                outcome=existing["outcome"],
                provider_operation_id=existing["provider_operation_id"],
                summary={"duplicate_delivery": True, **existing["summary"]},
                error_code=existing["error_code"],
            )

        if "credentials" in command.parameters:
            raise AdapterPreSendError("adapter accepts operational parameters only")

        # Atomic compare-and-mutate on the provider-side resource version.
        key = self._target_key(tool, command.parameters)
        current = self._target_versions.setdefault(key, "v1")
        if command.resource_version is not None and command.resource_version != current:
            raise AdapterPreSendError("target changed between revalidation and execution")

        provider_operation_id = _provider_operation_id(command.operation_key)

        if command.parameters.get("service") == _TIMEOUT_AFTER_SEND_SERVICE:
            # The send happened; the response was lost. Ambiguous.
            self._operations[command.operation_key] = {
                "tool": tool.value,
                "outcome": ExecutionOutcome.SUCCEEDED.value,
                "provider_operation_id": provider_operation_id,
                "summary": {"applied": True, "tool": tool.value},
                "error_code": None,
            }
            return AdapterResult(
                outcome=ExecutionOutcome.UNKNOWN.value,
                provider_operation_id=provider_operation_id,
                summary={"reason": "timeout_after_send", "tool": tool.value},
            )

        if command.parameters.get("service") == _PROVIDER_FAILURE_SERVICE:
            self._operations[command.operation_key] = {
                "tool": tool.value,
                "outcome": ExecutionOutcome.FAILED.value,
                "provider_operation_id": provider_operation_id,
                "summary": {"applied": False, "tool": tool.value},
                "error_code": "DEMO_PROVIDER_FAILURE",
            }
            return AdapterResult(
                outcome=ExecutionOutcome.FAILED.value,
                provider_operation_id=provider_operation_id,
                summary={"applied": False, "tool": tool.value},
                error_code="DEMO_PROVIDER_FAILURE",
            )

        self._operations[command.operation_key] = {
            "tool": tool.value,
            "outcome": ExecutionOutcome.SUCCEEDED.value,
            "provider_operation_id": provider_operation_id,
            "summary": {
                "applied": True,
                "tool": tool.value,
                "parameters": dict(command.parameters),
            },
            "error_code": None,
        }
        return AdapterResult(
            outcome=ExecutionOutcome.SUCCEEDED.value,
            provider_operation_id=provider_operation_id,
            summary={"applied": True, "tool": tool.value},
        )

    async def lookup_status(
        self, tool: ToolName, operation_key: str, provider_operation_id: str | None
    ) -> AdapterResult:
        record = self._operations.get(operation_key)
        if record is None:
            # No provider evidence: reconciliation must preserve UNKNOWN.
            return AdapterResult(
                outcome=ExecutionOutcome.UNKNOWN.value,
                provider_operation_id=provider_operation_id,
                summary={"evidence": "none", "tool": tool.value},
            )
        return AdapterResult(
            outcome=record["outcome"],
            provider_operation_id=record["provider_operation_id"],
            summary={"evidence": "provider_record", "tool": tool.value},
        )
