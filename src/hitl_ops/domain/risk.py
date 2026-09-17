"""Deterministic risk classification.

Risk is a pure function of the canonical intent and bounded evaluated context.
Rules may raise risk but never lower it below the tool minimum, and
``delete_resource`` stays CRITICAL in V1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hitl_ops.agent.schemas import UnknownToolError
from hitl_ops.domain.enums import RiskBand, ToolName

RISK_RULES_VERSION = "risk-rules-1"
BAND_ORDER: dict[RiskBand, int] = {
    RiskBand.LOW: 0,
    RiskBand.MEDIUM: 1,
    RiskBand.HIGH: 2,
    RiskBand.CRITICAL: 3,
}
TOOL_MINIMUM_BAND: dict[ToolName, RiskBand] = {
    ToolName.INSPECT_SERVICE: RiskBand.LOW,
    ToolName.RESTART_SERVICE: RiskBand.HIGH,
    ToolName.SCALE_SERVICE: RiskBand.MEDIUM,
    ToolName.PROVISION_RESOURCE: RiskBand.HIGH,
    ToolName.DELETE_RESOURCE: RiskBand.CRITICAL,
}
MAX_POLICY_FLAGS = 5


@dataclass(frozen=True, slots=True)
class RiskContext:
    """Bounded, explicit context; unknown context cannot enter evaluation."""

    degraded_dependencies: bool = False
    policy_flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RiskEvaluation:
    band: RiskBand
    factors: tuple[str, ...]
    rule_version: str
    evaluated_context: dict[str, Any]


_MUTATING_TOOLS = frozenset(
    {ToolName.RESTART_SERVICE, ToolName.SCALE_SERVICE, ToolName.PROVISION_RESOURCE}
)


def _raise_band(band: RiskBand, target_order: int) -> RiskBand:
    current = BAND_ORDER[band]
    if target_order <= current:
        return band
    for candidate, order in BAND_ORDER.items():
        if order == target_order:
            return candidate
    return RiskBand.CRITICAL


def evaluate_risk(
    tool: ToolName, parameters: dict[str, Any], context: RiskContext
) -> RiskEvaluation:
    if tool not in TOOL_MINIMUM_BAND:
        raise UnknownToolError(f"unknown tool: {tool}")
    factors: list[str] = []
    band = TOOL_MINIMUM_BAND[tool]

    environment = parameters.get("environment")
    if environment == "production" and tool in _MUTATING_TOOLS:
        band = _raise_band(band, BAND_ORDER[RiskBand.HIGH])
        factors.append("production_environment")

    if parameters.get("cost_class") == "high":
        band = _raise_band(band, BAND_ORDER[band] + 1)
        factors.append("high_cost")

    if context.degraded_dependencies:
        band = _raise_band(band, BAND_ORDER[band] + 1)
        factors.append("degraded_dependencies")

    for flag in context.policy_flags[:MAX_POLICY_FLAGS]:
        band = _raise_band(band, BAND_ORDER[band] + 1)
        factors.append(f"policy_flag:{flag}")

    return RiskEvaluation(
        band=band,
        factors=tuple(factors),
        rule_version=RISK_RULES_VERSION,
        evaluated_context={
            "environment": environment,
            "degraded_dependencies": context.degraded_dependencies,
            "policy_flags": list(context.policy_flags[:MAX_POLICY_FLAGS]),
        },
    )
