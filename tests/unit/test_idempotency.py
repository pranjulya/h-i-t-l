"""Idempotency semantics unit tests."""

from __future__ import annotations

from hitl_ops.application.commands import canonical_request_hash, idempotency_key_hash


def test_semantically_identical_requests_share_the_hash() -> None:
    a = {"tool": "scale_service", "parameters": {"service": "api", "replicas": 4}}
    b = {"parameters": {"replicas": 4, "service": "api"}, "tool": "scale_service"}
    assert canonical_request_hash(a) == canonical_request_hash(b)


def test_any_value_change_changes_the_hash() -> None:
    a = {"tool": "scale_service", "parameters": {"service": "api", "replicas": 4}}
    b = {"tool": "scale_service", "parameters": {"service": "api", "replicas": 5}}
    assert canonical_request_hash(a) != canonical_request_hash(b)


def test_key_hash_is_stable_and_unrevealing() -> None:
    assert idempotency_key_hash("abc") == idempotency_key_hash("abc")
    assert idempotency_key_hash("abc") != idempotency_key_hash("abd")
    assert len(idempotency_key_hash("abc")) == 64
