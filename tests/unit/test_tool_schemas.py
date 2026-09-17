"""Strict tool schema unit tests: the allow-list boundary for untrusted proposals."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hitl_ops.agent.schemas import (
    DeleteResourceParameters,
    InspectServiceParameters,
    ParameterValidationError,
    ProvisionResourceParameters,
    RestartServiceParameters,
    ScaleServiceParameters,
    UnknownToolError,
    parse_tool_parameters,
)
from hitl_ops.domain.enums import ToolName


def test_every_tool_has_a_registered_schema() -> None:
    from hitl_ops.agent.schemas import TOOL_PARAMETER_MODELS

    for tool in ToolName:
        assert tool.value in TOOL_PARAMETER_MODELS


def test_inspect_service_validates() -> None:
    parsed = parse_tool_parameters(
        "inspect_service",
        {"environment": "staging", "service": "api", "requested_fields": ["health", "replicas"]},
    )
    assert isinstance(parsed, InspectServiceParameters)


def test_restart_service_validates() -> None:
    parsed = parse_tool_parameters(
        "restart_service",
        {"environment": "production", "service": "payments-api", "strategy": "rolling"},
    )
    assert isinstance(parsed, RestartServiceParameters)


def test_scale_service_validates_bounds() -> None:
    parsed = parse_tool_parameters(
        "scale_service", {"environment": "staging", "service": "api", "replicas": 4}
    )
    assert isinstance(parsed, ScaleServiceParameters)
    with pytest.raises(ParameterValidationError):
        parse_tool_parameters(
            "scale_service", {"environment": "staging", "service": "api", "replicas": 1001}
        )
    with pytest.raises(ParameterValidationError):
        parse_tool_parameters(
            "scale_service", {"environment": "staging", "service": "api", "replicas": -1}
        )


def test_provision_resource_validates() -> None:
    parsed = parse_tool_parameters(
        "provision_resource",
        {
            "environment": "staging",
            "resource_type": "redis_cache",
            "name": "cache-1",
            "region": "us-east-1",
            "size": "small",
            "cost_class": "low",
        },
    )
    assert isinstance(parsed, ProvisionResourceParameters)


def test_delete_resource_validates() -> None:
    parsed = parse_tool_parameters(
        "delete_resource",
        {
            "environment": "production",
            "resource_type": "postgres_instance",
            "resource_id": "pg-main-1",
            "deletion_mode": "hard",
        },
    )
    assert isinstance(parsed, DeleteResourceParameters)


def test_unknown_tool_is_rejected() -> None:
    with pytest.raises(UnknownToolError):
        parse_tool_parameters("deploy_to_production", {})


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ParameterValidationError):
        parse_tool_parameters(
            "scale_service",
            {"environment": "staging", "service": "api", "replicas": 2, "force": True},
        )


def test_invalid_identifier_patterns_are_rejected() -> None:
    with pytest.raises(ParameterValidationError):
        parse_tool_parameters(
            "scale_service", {"environment": "staging", "service": "Payments API", "replicas": 2}
        )


def test_closed_enums_reject_invented_values() -> None:
    with pytest.raises(ParameterValidationError):
        parse_tool_parameters(
            "restart_service",
            {"environment": "staging", "service": "api", "strategy": "chaos"},
        )
    with pytest.raises(ParameterValidationError):
        parse_tool_parameters(
            "delete_resource",
            {
                "environment": "staging",
                "resource_type": "postgres_instance",
                "resource_id": "pg-1",
                "deletion_mode": "nuclear",
            },
        )


def test_requested_fields_are_bounded() -> None:
    with pytest.raises(ValidationError):
        InspectServiceParameters.model_validate(
            {
                "environment": "staging",
                "service": "api",
                "requested_fields": [f"field_{i}" for i in range(11)],
            }
        )
