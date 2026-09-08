"""Live target query contract.

Read-only, bounded queries against the target platform used by revalidation.
Adapters translate typed operations; they never own policy or approval logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from hitl_ops.domain.enums import ToolName


@dataclass(frozen=True, slots=True)
class TargetSnapshot:
    """Bounded live facts about the exact target of an approved intent."""

    found: bool
    identity: dict[str, Any] = field(default_factory=dict)
    health: str = "unknown"
    facts: dict[str, Any] = field(default_factory=dict)


class LiveTargetQuery(Protocol):
    """A read-only query; implementations must be bounded and side-effect free."""

    async def fetch(self, tool: ToolName, parameters: dict[str, Any]) -> TargetSnapshot: ...


class DenyingTargetQuery:
    """Fail-closed query used when no live platform is configured."""

    async def fetch(self, tool: ToolName, parameters: dict[str, Any]) -> TargetSnapshot:
        return TargetSnapshot(found=False, identity={}, health="unknown", facts={})


class AdapterPreSendError(Exception):
    """The adapter rejected the operation before any provider interaction."""


class AdapterUnavailableError(Exception):
    """The provider could not be reached; whether a send happened is unknown."""


@dataclass(frozen=True, slots=True)
class AdapterCommand:
    """Typed command data; adapters never receive tool names plus arbitrary dicts."""

    operation_key: str
    precondition_token: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AdapterResult:
    """Sanitized provider outcome evidence."""

    outcome: str  # ExecutionOutcome value: SUCCEEDED | FAILED | UNKNOWN
    provider_operation_id: str | None
    summary: dict[str, Any]
    error_code: str | None = None


class InfrastructureAdapter(Protocol):
    """The only privileged infrastructure path; implementations own credentials."""

    async def execute(self, tool: ToolName, command: AdapterCommand) -> AdapterResult: ...

    async def lookup_status(
        self, tool: ToolName, operation_key: str, provider_operation_id: str | None
    ) -> AdapterResult:
        """Return provider evidence for reconciliation, or outcome UNKNOWN."""
        ...
