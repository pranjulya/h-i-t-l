"""Revalidation pure-logic unit tests."""

from __future__ import annotations

import uuid

from hitl_ops.adapters.base import DenyingTargetQuery, TargetSnapshot
from hitl_ops.application.revalidation import (
    check_preconditions,
    is_stricter,
    operation_key_for,
    policy_stricter,
    recompute_digest,
    route_stricter,
)
from hitl_ops.domain.enums import ApprovalRoute
from hitl_ops.infrastructure.orm import ActionIntentORM


def _intent(**overrides: object) -> ActionIntentORM:
    values: dict = {
        "tenant_id": "tenant-1",
        "intent_id": uuid.uuid4(),
        "revision": 1,
        "tool": "scale_service",
        "canonical_parameters": {"environment": "staging", "replicas": 4, "service": "api"},
        "requester_id": "user-1",
    }
    values.update(overrides)
    return ActionIntentORM(**values)  # type: ignore[arg-type]


def test_recompute_digest_matches_canonicalization() -> None:
    from hitl_ops.agent.schemas import parse_tool_parameters
    from hitl_ops.domain.enums import ToolName
    from hitl_ops.domain.intent import canonicalize

    intent = _intent()
    parsed = parse_tool_parameters("scale_service", intent.canonical_parameters)
    canonical = canonicalize(ToolName.SCALE_SERVICE, "tenant-1", "user-1", 1, parsed)
    assert recompute_digest(intent) == canonical.digest


def test_any_parameter_change_changes_recomputed_digest() -> None:
    base = recompute_digest(_intent())
    changed = recompute_digest(
        _intent(canonical_parameters={"environment": "staging", "replicas": 5, "service": "api"})
    )
    assert base != changed


def test_disposition_strictness_is_ordered() -> None:
    assert not is_stricter("ALLOW", "ALLOW")
    assert is_stricter("REQUIRE_APPROVAL", "ALLOW")
    assert is_stricter("BLOCK", "REQUIRE_APPROVAL")
    assert not is_stricter("ALLOW", "BLOCK")


def test_route_strictness_is_ordered() -> None:
    assert not route_stricter(ApprovalRoute.NONE, ApprovalRoute.SINGLE)
    assert route_stricter(ApprovalRoute.SINGLE, ApprovalRoute.NONE)
    assert route_stricter(ApprovalRoute.CRITICAL_TWO_STEP, ApprovalRoute.SINGLE)


def test_precondition_reasons_are_deterministic() -> None:
    healthy = TargetSnapshot(found=True, identity={"service": "api"}, health="healthy")
    assert check_preconditions("scale_service", {"service": "api"}, healthy) is None
    assert (
        check_preconditions("scale_service", {"service": "api"}, TargetSnapshot(found=False))
        == "target_missing"
    )
    assert (
        check_preconditions(
            "scale_service", {"service": "api"}, TargetSnapshot(found=True, health="degraded")
        )
        == "target_degraded"
    )
    replaced = TargetSnapshot(found=True, identity={"service": "other"}, health="healthy")
    assert check_preconditions("scale_service", {"service": "api"}, replaced) == "target_replaced"


def test_operation_key_is_stable_per_revision() -> None:
    intent_id = uuid.uuid4()
    assert operation_key_for("tenant-1", intent_id, 1) == operation_key_for(
        "tenant-1", intent_id, 1
    )
    assert operation_key_for("tenant-1", intent_id, 1) != operation_key_for(
        "tenant-1", intent_id, 2
    )


async def test_denying_target_query_fails_closed() -> None:
    snapshot = await DenyingTargetQuery().fetch("scale_service", {})  # type: ignore[arg-type]
    assert snapshot.found is False
    assert check_preconditions("scale_service", {}, snapshot) == "target_missing"


def test_missing_identity_field_fails_closed() -> None:
    # The approved intent names an exact service, but the live snapshot omits
    # that identity field entirely: this must be treated as a replaced target.
    snapshot = TargetSnapshot(found=True, identity={}, health="healthy")
    assert check_preconditions("scale_service", {"service": "api"}, snapshot) == "target_replaced"


def test_policy_stricter_detects_same_route_tightening() -> None:
    from types import SimpleNamespace

    from hitl_ops.domain.enums import PolicyDisposition

    def policy(**overrides: object) -> object:
        base: dict = {
            "disposition": PolicyDisposition.REQUIRE_APPROVAL,
            "route": ApprovalRoute.SINGLE,
            "required_roles": ["approver"],
            "required_scopes": ["ops:write"],
            "obligations": [],
            "approval_ttl_seconds": 900,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    original = policy()
    assert not policy_stricter(policy(), original)

    # Additional required scope under the same route stales.
    assert policy_stricter(policy(required_scopes=["ops:write", "ops:read"]), original)
    # Additional obligation under the same route stales.
    assert policy_stricter(policy(obligations=["require_change_ticket"]), original)
    # Shorter approval TTL under the same route stales.
    assert policy_stricter(policy(approval_ttl_seconds=300), original)
