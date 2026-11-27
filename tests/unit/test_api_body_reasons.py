"""Mutation bodies must carry a real reason, not whitespace."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from hitl_ops.api.admin import (
    ActivateBundleBody,
    GrantRoleBody,
    RegisterBundleBody,
    RevokeRoleBody,
)
from hitl_ops.api.approvals import ApprovalBody, CancelBody

_DIGEST = "a" * 64

_VALID: list[tuple[type[BaseModel], dict[str, Any]]] = [
    (
        ApprovalBody,
        {
            "revision": 1,
            "intent_digest": _DIGEST,
            "level": 1,
            "decision": "APPROVE",
            "reason": "checked the change window",
            "expected_state_version": 1,
        },
    ),
    (CancelBody, {"revision": 1, "reason": "no longer needed", "expected_state_version": 1}),
    (GrantRoleBody, {"principal_id": "user-1", "role": "approver", "reason": "on-call rotation"}),
    (RevokeRoleBody, {"reason": "rotation ended"}),
    (RegisterBundleBody, {"version": "v2", "rules": {}, "reason": "tighten prod rules"}),
    (ActivateBundleBody, {"reason": "approved in change review"}),
]


@pytest.mark.parametrize(("model", "valid"), _VALID, ids=[m.__name__ for m, _ in _VALID])
def test_valid_body_is_accepted(model: type[BaseModel], valid: dict[str, Any]) -> None:
    model.model_validate(valid)


@pytest.mark.parametrize(("model", "valid"), _VALID, ids=[m.__name__ for m, _ in _VALID])
@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_blank_reason_is_rejected(
    model: type[BaseModel], valid: dict[str, Any], blank: str
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(valid | {"reason": blank})


@pytest.mark.parametrize("field", ["principal_id", "role"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_grant_role_rejects_blank_principal_or_role(field: str, blank: str) -> None:
    valid = {"principal_id": "user-1", "role": "approver", "reason": "on-call rotation"}
    with pytest.raises(ValidationError):
        GrantRoleBody.model_validate(valid | {field: blank})
