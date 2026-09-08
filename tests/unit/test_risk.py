"""Deterministic risk engine unit tests."""

from __future__ import annotations

import itertools

import pytest

from hitl_ops.agent.schemas import UnknownToolError
from hitl_ops.domain.enums import RiskBand, ToolName
from hitl_ops.domain.risk import BAND_ORDER, RiskContext, evaluate_risk

_SCALE_PARAMS = {"environment": "staging", "service": "api", "replicas": 4}


def test_tool_minimums_are_enforced() -> None:
    assert (
        evaluate_risk(
            ToolName.INSPECT_SERVICE, {"environment": "staging", "service": "api"}, RiskContext()
        ).band
        is RiskBand.LOW
    )
    assert (
        evaluate_risk(ToolName.SCALE_SERVICE, _SCALE_PARAMS, RiskContext()).band is RiskBand.MEDIUM
    )
    assert (
        evaluate_risk(
            ToolName.RESTART_SERVICE,
            {"environment": "staging", "service": "api", "strategy": "rolling"},
            RiskContext(),
        ).band
        is RiskBand.HIGH
    )
    assert evaluate_risk(ToolName.DELETE_RESOURCE, {}, RiskContext()).band is RiskBand.CRITICAL


def test_production_escalates_to_high() -> None:
    evaluation = evaluate_risk(
        ToolName.SCALE_SERVICE,
        {"environment": "production", "service": "api", "replicas": 4},
        RiskContext(),
    )
    assert evaluation.band is RiskBand.HIGH
    assert "production_environment" in evaluation.factors


def test_high_cost_and_degraded_dependencies_escalate() -> None:
    evaluation = evaluate_risk(
        ToolName.PROVISION_RESOURCE,
        {
            "environment": "staging",
            "resource_type": "postgres_instance",
            "name": "pg-1",
            "region": "us-east-1",
            "size": "large",
            "cost_class": "high",
        },
        RiskContext(degraded_dependencies=True),
    )
    assert evaluation.band is RiskBand.CRITICAL
    assert "high_cost" in evaluation.factors
    assert "degraded_dependencies" in evaluation.factors


def test_policy_flags_escalate_and_are_bounded() -> None:
    evaluation = evaluate_risk(
        ToolName.INSPECT_SERVICE,
        {"environment": "staging", "service": "api"},
        RiskContext(policy_flags=tuple(f"flag-{i}" for i in range(10))),
    )
    assert evaluation.band is RiskBand.CRITICAL
    assert len([f for f in evaluation.factors if f.startswith("policy_flag:")]) == 5
    assert len(evaluation.evaluated_context["policy_flags"]) == 5


def test_delete_resource_is_always_critical() -> None:
    evaluation = evaluate_risk(ToolName.DELETE_RESOURCE, {"environment": "staging"}, RiskContext())
    assert evaluation.band is RiskBand.CRITICAL
    assert "production_environment" not in evaluation.factors


def test_unknown_tool_fails_closed() -> None:
    with pytest.raises(UnknownToolError):
        evaluate_risk("deploy_to_production", {}, RiskContext())


def test_evaluated_context_is_bounded_and_explicit() -> None:
    evaluation = evaluate_risk(ToolName.SCALE_SERVICE, _SCALE_PARAMS, RiskContext())
    assert set(evaluation.evaluated_context) == {
        "environment",
        "degraded_dependencies",
        "policy_flags",
    }
    assert evaluation.rule_version == "risk-rules-1"


def test_added_factors_never_reduce_risk() -> None:
    """Property: any superset of context flags produces an equal or higher band."""

    base_contexts = [
        RiskContext(),
        RiskContext(degraded_dependencies=True),
        RiskContext(policy_flags=("f1",)),
        RiskContext(degraded_dependencies=True, policy_flags=("f1", "f2")),
    ]
    for tool, params in (
        (ToolName.SCALE_SERVICE, _SCALE_PARAMS),
        (
            ToolName.RESTART_SERVICE,
            {"environment": "staging", "service": "api", "strategy": "rolling"},
        ),
    ):
        bands = [BAND_ORDER[evaluate_risk(tool, params, context).band] for context in base_contexts]
        assert bands == sorted(bands), bands


def test_all_factor_combinations_stay_within_band_scale() -> None:
    flags = ("f1", "f2")
    for deps in (False, True):
        for subset in itertools.chain.from_iterable(
            itertools.combinations(flags, size) for size in range(len(flags) + 1)
        ):
            evaluation = evaluate_risk(
                ToolName.INSPECT_SERVICE,
                {"environment": "staging", "service": "api"},
                RiskContext(degraded_dependencies=deps, policy_flags=subset),
            )
            assert evaluation.band in RiskBand
