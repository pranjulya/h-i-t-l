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
