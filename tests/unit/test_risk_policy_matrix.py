"""Full tool x environment risk/policy matrix against the seed bundle."""

from __future__ import annotations

import pytest

from hitl_ops.domain.enums import ApprovalRoute, PolicyDisposition, RiskBand, ToolName
from hitl_ops.domain.policy import (
    SEED_POLICY_BUNDLE_RULES,
    SEED_POLICY_BUNDLE_VERSION,
    PolicyBundle,
    evaluate_policy,
)
from hitl_ops.domain.risk import RiskContext, evaluate_risk

BUNDLE = PolicyBundle(version=SEED_POLICY_BUNDLE_VERSION, rules=SEED_POLICY_BUNDLE_RULES)

_PARAMETERS = {
    ToolName.INSPECT_SERVICE: {"environment": "staging", "service": "api"},
    ToolName.RESTART_SERVICE: {"environment": "staging", "service": "api", "strategy": "rolling"},
    ToolName.SCALE_SERVICE: {"environment": "staging", "service": "api", "replicas": 4},
    ToolName.PROVISION_RESOURCE: {
        "environment": "staging",
        "resource_type": "postgres_instance",
        "name": "pg-1",
        "region": "us-east-1",
        "size": "medium",
        "cost_class": "medium",
    },
    ToolName.DELETE_RESOURCE: {
        "environment": "staging",
        "resource_type": "postgres_instance",
        "resource_id": "pg-1",
        "deletion_mode": "soft",
    },
}


def _expected(
    tool: ToolName, environment: str
) -> tuple[RiskBand, PolicyDisposition, ApprovalRoute]:
    parameters = {**_PARAMETERS[tool], "environment": environment}
    risk = evaluate_risk(tool, parameters, RiskContext())
    policy = evaluate_policy(tool, risk, parameters, BUNDLE)
    return risk.band, policy.disposition, policy.route


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_inspect_is_low_and_auto_allowed(environment: str) -> None:
    band, disposition, route = _expected(ToolName.INSPECT_SERVICE, environment)
    assert (band, disposition, route) == (RiskBand.LOW, PolicyDisposition.ALLOW, ApprovalRoute.NONE)


def test_scale_matrix() -> None:
    assert _expected(ToolName.SCALE_SERVICE, "staging") == (
        RiskBand.MEDIUM,
        PolicyDisposition.ALLOW,
        ApprovalRoute.NONE,
    )
    # Production escalates the band to HIGH, so the approval route is enforced.
    assert _expected(ToolName.SCALE_SERVICE, "production") == (
        RiskBand.HIGH,
        PolicyDisposition.REQUIRE_APPROVAL,
        ApprovalRoute.SINGLE,
    )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_restart_requires_single_approval(environment: str) -> None:
    band, disposition, route = _expected(ToolName.RESTART_SERVICE, environment)
    assert band is RiskBand.HIGH
    assert (disposition, route) == (
        PolicyDisposition.REQUIRE_APPROVAL,
        ApprovalRoute.SINGLE,
    )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_provision_requires_single_approval(environment: str) -> None:
    band, disposition, route = _expected(ToolName.PROVISION_RESOURCE, environment)
    assert band is RiskBand.HIGH
    assert (disposition, route) == (
        PolicyDisposition.REQUIRE_APPROVAL,
        ApprovalRoute.SINGLE,
    )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_delete_is_always_critical_two_step(environment: str) -> None:
    band, disposition, route = _expected(ToolName.DELETE_RESOURCE, environment)
    assert (band, disposition, route) == (
        RiskBand.CRITICAL,
        PolicyDisposition.REQUIRE_APPROVAL,
        ApprovalRoute.CRITICAL_TWO_STEP,
    )


def test_production_mutations_meet_or_exceed_minimum() -> None:
    for tool in ToolName:
        band, _, route = _expected(tool, "production")
        if tool is ToolName.INSPECT_SERVICE:
            continue
        assert band in (RiskBand.HIGH, RiskBand.CRITICAL)
        assert route in (ApprovalRoute.SINGLE, ApprovalRoute.CRITICAL_TWO_STEP)


def test_every_tool_maps_without_model_rationale() -> None:
    for tool in ToolName:
        risk = evaluate_risk(tool, _PARAMETERS[tool], RiskContext())
        policy = evaluate_policy(tool, risk, _PARAMETERS[tool], BUNDLE)
        assert policy.policy_version == SEED_POLICY_BUNDLE_VERSION
