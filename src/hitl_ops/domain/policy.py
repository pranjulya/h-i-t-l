"""Versioned policy evaluation.

Policy is versioned data evaluated deterministically. It can never lower the
tool's minimum approval treatment: an ALLOW decision against a HIGH or
CRITICAL band is coerced back to the required approval route with a reason
code. Missing rules, invalid TTLs, and malformed bundles fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hitl_ops.agent.schemas import UnknownToolError
from hitl_ops.domain.enums import ApprovalRoute, PolicyDisposition, RiskBand, ToolName
from hitl_ops.domain.risk import RiskEvaluation

DEFAULT_APPROVAL_TTL_SECONDS = 900
MAX_APPROVAL_TTL_SECONDS = 3600
_DEFAULT_SCOPES: dict[str, tuple[str, ...]] = {
    ToolName.INSPECT_SERVICE.value: ("ops:read",),
}
_FALLBACK_SCOPES: tuple[str, ...] = ("ops:write",)
_CRITICAL_ROLES: tuple[str, ...] = ("critical_approver_l1", "critical_approver_l2")
_SINGLE_APPROVAL_ROLES: tuple[str, ...] = ("approver",)


@dataclass(frozen=True, slots=True)
class PolicyBundle:
    """Immutable versioned policy data; contents are authored in source control."""

    version: str
    rules: dict[str, Any]


SEED_POLICY_BUNDLE_VERSION = "policy-1"
SEED_POLICY_BUNDLE_RULES: dict[str, Any] = {
    "approval_ttl_seconds": DEFAULT_APPROVAL_TTL_SECONDS,
    "tools": {
        "inspect_service": {"disposition": "ALLOW"},
        "restart_service": {
            "disposition": "REQUIRE_APPROVAL",
            "required_roles": ["approver"],
            "obligations": ["announce_in_incident_channel"],
        },
        "scale_service": {
            "staging": {"disposition": "ALLOW"},
            "production": {"disposition": "REQUIRE_APPROVAL", "required_roles": ["approver"]},
        },
        "provision_resource": {"disposition": "REQUIRE_APPROVAL", "required_roles": ["approver"]},
        "delete_resource": {
            "disposition": "REQUIRE_APPROVAL",
            "required_roles": ["critical_approver_l1", "critical_approver_l2"],
            "obligations": ["require_change_ticket"],
        },
    },
}


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    disposition: PolicyDisposition
    route: ApprovalRoute
    required_roles: tuple[str, ...]
    required_scopes: tuple[str, ...]
    obligations: tuple[str, ...]
    reason_codes: tuple[str, ...]
    policy_version: str
    approval_ttl_seconds: int | None


def _blocked(reason: str, policy_version: str) -> PolicyEvaluation:
    return PolicyEvaluation(
        disposition=PolicyDisposition.BLOCK,
        route=ApprovalRoute.NONE,
        required_roles=(),
        required_scopes=(),
        obligations=(),
        reason_codes=(reason,),
        policy_version=policy_version,
        approval_ttl_seconds=None,
    )


def _tool_rule(bundle: PolicyBundle, tool: ToolName, environment: Any) -> dict[str, Any] | None:
    rules = bundle.rules.get("tools")
    if not isinstance(rules, dict):
        return None
    rule = rules.get(tool.value)
    if not isinstance(rule, dict):
        return None
    if isinstance(environment, str) and isinstance(rule.get(environment), dict):
        merged = {key: value for key, value in rule.items() if key not in ("staging", "production")}
        merged.update(rule[environment])
        return merged
    return rule


def evaluate_policy(
    tool: ToolName, risk: RiskEvaluation, parameters: dict[str, Any], bundle: PolicyBundle
) -> PolicyEvaluation:
    if tool not in ToolName:
        raise UnknownToolError(f"unknown tool: {tool}")
    rule = _tool_rule(bundle, tool, parameters.get("environment"))
    if rule is None:
        return _blocked("no_policy_rule", bundle.version)

    ttl = rule.get(
        "approval_ttl_seconds",
        bundle.rules.get("approval_ttl_seconds", DEFAULT_APPROVAL_TTL_SECONDS),
    )
    if (
        not isinstance(ttl, int)
        or isinstance(ttl, bool)
        or ttl <= 0
        or ttl > MAX_APPROVAL_TTL_SECONDS
    ):
        return _blocked("invalid_ttl", bundle.version)

    roles = tuple(str(role) for role in rule.get("required_roles", ()))
    scopes = tuple(
        str(scope)
        for scope in rule.get("scopes", _DEFAULT_SCOPES.get(tool.value, _FALLBACK_SCOPES))
    )
    obligations = tuple(str(item) for item in rule.get("obligations", ()))
    raw_disposition = str(rule.get("disposition", "REQUIRE_APPROVAL"))
    reason_codes: list[str] = []

    if risk.band is RiskBand.CRITICAL:
        if raw_disposition == "BLOCK":
            disposition = PolicyDisposition.BLOCK
        else:
            disposition = PolicyDisposition.REQUIRE_APPROVAL
            if raw_disposition == "ALLOW":
                reason_codes.append("tool_minimum_enforced")
        route = (
            ApprovalRoute.NONE
            if disposition is PolicyDisposition.BLOCK
            else ApprovalRoute.CRITICAL_TWO_STEP
        )
        if not roles:
            roles = _CRITICAL_ROLES
    elif risk.band is RiskBand.HIGH:
        if raw_disposition == "BLOCK":
            disposition = PolicyDisposition.BLOCK
        else:
            disposition = PolicyDisposition.REQUIRE_APPROVAL
            if raw_disposition == "ALLOW":
                reason_codes.append("tool_minimum_enforced")
        route = (
            ApprovalRoute.NONE if disposition is PolicyDisposition.BLOCK else ApprovalRoute.SINGLE
        )
        if not roles:
            roles = _SINGLE_APPROVAL_ROLES
    else:
        if raw_disposition == "BLOCK":
            disposition = PolicyDisposition.BLOCK
        elif raw_disposition == "ALLOW":
            disposition = PolicyDisposition.ALLOW
        elif raw_disposition == "REQUIRE_APPROVAL":
            disposition = PolicyDisposition.REQUIRE_APPROVAL
        else:
            return _blocked("unknown_disposition", bundle.version)
        route = (
            ApprovalRoute.SINGLE
            if disposition is PolicyDisposition.REQUIRE_APPROVAL
            else ApprovalRoute.NONE
        )
        if disposition is PolicyDisposition.REQUIRE_APPROVAL and not roles:
            roles = _SINGLE_APPROVAL_ROLES

    if disposition is PolicyDisposition.BLOCK:
        route = ApprovalRoute.NONE
        reason_codes.append("policy_blocked")
    elif disposition is PolicyDisposition.REQUIRE_APPROVAL:
        reason_codes.append("approval_required")

    return PolicyEvaluation(
        disposition=disposition,
        route=route,
        required_roles=roles,
        required_scopes=scopes,
        obligations=obligations,
        reason_codes=tuple(reason_codes),
        policy_version=bundle.version,
        approval_ttl_seconds=ttl if disposition is PolicyDisposition.REQUIRE_APPROVAL else None,
    )
