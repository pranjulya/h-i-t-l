"""Canonical intent, digest binding, and raw-proposal evidence unit tests."""

from __future__ import annotations

import pytest

from hitl_ops.agent.schemas import parse_tool_parameters
from hitl_ops.domain.enums import ToolName
from hitl_ops.domain.intent import CanonicalIntent, canonicalize, sanitize_raw_proposal

_GOLDEN_CANONICAL_JSON = (
    '{"digest_version":"1","parameters":{"environment":"staging","replicas":4,"service":"api"},'
    '"requester_id":"user-1","revision":1,"tenant_id":"tenant-1","tool":"scale_service"}'
)
_GOLDEN_DIGEST = "36711882d002d162087aaa02d6a7c963550b42b110029dfa4e58a35385e4af7e"


def _scale_intent(
    tenant_id: str = "tenant-1", requester_id: str = "user-1", revision: int = 1
) -> CanonicalIntent:
    parameters = parse_tool_parameters(
        "scale_service", {"environment": "staging", "service": "api", "replicas": 4}
    )
    return canonicalize(ToolName.SCALE_SERVICE, tenant_id, requester_id, revision, parameters)


def test_golden_digest_vector() -> None:
    intent = _scale_intent()
    assert intent.canonical_json == _GOLDEN_CANONICAL_JSON
    assert intent.digest == _GOLDEN_DIGEST


def test_key_order_does_not_change_digest() -> None:
    direct = _scale_intent()
    # The same validated values arrive via a different construction order.
    reordered = parse_tool_parameters(
        "scale_service", {"replicas": 4, "service": "api", "environment": "staging"}
    )
    other = canonicalize(ToolName.SCALE_SERVICE, "tenant-1", "user-1", 1, reordered)
    assert direct.digest == other.digest


def test_unicode_normalization_is_canonical() -> None:
    decomposed = _scale_intent(requester_id="cafe\u0301-user")
    precomposed = _scale_intent(requester_id="caf\u00e9-user")
    assert decomposed.digest == precomposed.digest


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("tenant_id", "tenant-2"),
        ("requester_id", "user-2"),
    ],
)
def test_identity_changes_change_digest(field_name: str, value: str) -> None:
    base = _scale_intent()
    changed = _scale_intent(**{field_name: value})
    assert base.digest != changed.digest


def test_revision_and_material_changes_change_digest() -> None:
    base = _scale_intent()
    changed_revision = _scale_intent(revision=2)
    assert base.digest != changed_revision.digest
    changed_parameters = parse_tool_parameters(
        "scale_service", {"environment": "staging", "service": "api", "replicas": 5}
    )
    changed = canonicalize(ToolName.SCALE_SERVICE, "tenant-1", "user-1", 1, changed_parameters)
    assert base.digest != changed.digest


def test_revision_below_one_is_rejected() -> None:
    parameters = parse_tool_parameters(
        "scale_service", {"environment": "staging", "service": "api", "replicas": 4}
    )
    with pytest.raises(ValueError):
        canonicalize(ToolName.SCALE_SERVICE, "tenant-1", "user-1", 0, parameters)


def test_model_tool_mismatch_is_rejected() -> None:
    parameters = parse_tool_parameters(
        "scale_service", {"environment": "staging", "service": "api", "replicas": 4}
    )
    with pytest.raises(ValueError):
        canonicalize(ToolName.INSPECT_SERVICE, "tenant-1", "user-1", 1, parameters)


def test_secret_looking_keys_are_redacted() -> None:
    sanitized = sanitize_raw_proposal(
        {
            "service": "api",
            "Password": "hunter2",
            "api_key": "sk-123",
            "nested": {"AUTHORIZATION": "Bearer abc", "safe": "value"},
            "items": [{"token": "t-1"}, "plain"],
        }
    )
    assert sanitized["service"] == "api"
    assert sanitized["Password"] == "[REDACTED]"
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["nested"]["AUTHORIZATION"] == "[REDACTED]"
    assert sanitized["nested"]["safe"] == "value"
    assert sanitized["items"][0]["token"] == "[REDACTED]"
    assert sanitized["items"][1] == "plain"
    assert "hunter2" not in str(sanitized)


def test_oversized_proposal_is_truncated_with_marker() -> None:
    sanitized = sanitize_raw_proposal({"blob": "x" * 40000, "service": "api"})
    assert sanitized["_truncated"] is True
    assert sanitized["_original_bytes"] > 16384
    assert "_preview" in sanitized
    assert len(str(sanitized["_preview"])) <= 4096
