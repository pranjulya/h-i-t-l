"""Versioned policy engine unit tests."""

from __future__ import annotations

import pytest

from hitl_ops.agent.schemas import UnknownToolError
from hitl_ops.domain.enums import ApprovalRoute, PolicyDisposition, RiskBand, ToolName
from hitl_ops.domain.policy import (
    SEED_POLICY_BUNDLE_RULES,
    SEED_POLICY_BUNDLE_VERSION,
    PolicyBundle,
    PolicyEvaluation,
    evaluate_policy,
)
from hitl_ops.domain.risk import RiskEvaluation


def _bundle(rules: dict) -> PolicyBundle:
    return PolicyBundle(version=SEED_POLICY_BUNDLE_VERSION, rules=rules)


def _risk(band: RiskBand) -> RiskEvaluation:
    return RiskEvaluation(band=band, factors=(), rule_version="risk-rules-1", evaluated_context={})


def test_low_with_allow_routes_none() -> None:
    evaluation = evaluate_policy(
        ToolName.INSPECT_SERVICE,
        _risk(RiskBand.LOW),
        {"environment": "staging", "service": "api"},
        _bundle(SEED_POLICY_BUNDLE_RULES),
    )
    assert evaluation.disposition is PolicyDisposition.ALLOW
    assert evaluation.route is ApprovalRoute.NONE
    assert evaluation.required_scopes == ("ops:read",)
    assert evaluation.approval_ttl_seconds is None


def test_medium_policy_decides_the_branch() -> None:
    staging = evaluate_policy(
        ToolName.SCALE_SERVICE,
        _risk(RiskBand.MEDIUM),
        {"environment": "staging", "service": "api", "replicas": 4},
        _bundle(SEED_POLICY_BUNDLE_RULES),
    )
    assert staging.disposition is PolicyDisposition.ALLOW
    assert staging.route is ApprovalRoute.NONE


def test_policy_cannot_lower_the_tool_minimum() -> None:
    rules = {"tools": {"restart_service": {"disposition": "ALLOW"}}}
    evaluation = evaluate_policy(
        ToolName.RESTART_SERVICE,
        _risk(RiskBand.HIGH),
        {"environment": "staging", "service": "api", "strategy": "rolling"},
        _bundle(rules),
    )
    assert evaluation.disposition is PolicyDisposition.REQUIRE_APPROVAL
    assert evaluation.route is ApprovalRoute.SINGLE
    assert "tool_minimum_enforced" in evaluation.reason_codes
    assert evaluation.required_roles == ("approver",)


def test_critical_requires_two_step_with_distinct_roles() -> None:
    evaluation = evaluate_policy(
        ToolName.DELETE_RESOURCE,
        _risk(RiskBand.CRITICAL),
        {"environment": "production"},
        _bundle(SEED_POLICY_BUNDLE_RULES),
    )
    assert evaluation.route is ApprovalRoute.CRITICAL_TWO_STEP
    assert evaluation.required_roles == ("critical_approver_l1", "critical_approver_l2")
    assert evaluation.obligations == ("require_change_ticket",)
    assert evaluation.approval_ttl_seconds == 900


def test_missing_rule_fails_closed() -> None:
    rules = {"tools": {"inspect_service": {"disposition": "ALLOW"}}}
    evaluation = evaluate_policy(
        ToolName.SCALE_SERVICE, _risk(RiskBand.MEDIUM), {"environment": "staging"}, _bundle(rules)
    )
    assert evaluation.disposition is PolicyDisposition.BLOCK
    assert evaluation.route is ApprovalRoute.NONE
    assert evaluation.reason_codes == ("no_policy_rule",)


def test_invalid_ttl_fails_closed() -> None:
    for bad_ttl in (0, -5, 3601, "900", True):
        rules = {
            "tools": {"inspect_service": {"disposition": "ALLOW", "approval_ttl_seconds": bad_ttl}}
        }
        evaluation = evaluate_policy(
            ToolName.INSPECT_SERVICE, _risk(RiskBand.LOW), {}, _bundle(rules)
        )
        assert evaluation.disposition is PolicyDisposition.BLOCK, bad_ttl
        assert evaluation.reason_codes == ("invalid_ttl",)


def test_unknown_disposition_fails_closed() -> None:
    rules = {"tools": {"inspect_service": {"disposition": "MAYBE"}}}
    evaluation = evaluate_policy(ToolName.INSPECT_SERVICE, _risk(RiskBand.LOW), {}, _bundle(rules))
    assert evaluation.reason_codes == ("unknown_disposition",)


def test_malformed_bundle_fails_closed() -> None:
    evaluation = evaluate_policy(
        ToolName.INSPECT_SERVICE, _risk(RiskBand.LOW), {}, _bundle({"tools": "not-a-dict"})
    )
    assert evaluation.disposition is PolicyDisposition.BLOCK


def test_environment_override_is_applied() -> None:
    rules = {
        "tools": {
            "scale_service": {
                "disposition": "BLOCK",
                "staging": {"disposition": "ALLOW"},
            }
        }
    }
    staging = evaluate_policy(
        ToolName.SCALE_SERVICE, _risk(RiskBand.MEDIUM), {"environment": "staging"}, _bundle(rules)
    )
    production = evaluate_policy(
        ToolName.SCALE_SERVICE,
        _risk(RiskBand.MEDIUM),
        {"environment": "production"},
        _bundle(rules),
    )
    assert staging.disposition is PolicyDisposition.ALLOW
    assert production.disposition is PolicyDisposition.BLOCK


def test_unknown_tool_is_rejected() -> None:
    with pytest.raises(UnknownToolError):
        evaluate_policy("deploy", _risk(RiskBand.LOW), {}, _bundle(SEED_POLICY_BUNDLE_RULES))


def test_blocked_policy_has_no_ttl() -> None:
    rules = {"tools": {"scale_service": {"disposition": "BLOCK"}}}
    evaluation: PolicyEvaluation = evaluate_policy(
        ToolName.SCALE_SERVICE, _risk(RiskBand.MEDIUM), {"environment": "staging"}, _bundle(rules)
    )
    assert evaluation.approval_ttl_seconds is None
