"""Outcome classification unit tests."""

from __future__ import annotations

from hitl_ops.adapters.base import AdapterPreSendError, AdapterResult
from hitl_ops.application.execution import classify_outcome
from hitl_ops.domain.enums import ExecutionOutcome


def _result(outcome: str) -> AdapterResult:
    return AdapterResult(outcome=outcome, provider_operation_id="prov-1", summary={"applied": True})


def test_confirmed_success_maps_directly() -> None:
    outcome, error_code = classify_outcome(_result("SUCCEEDED"), None)
    assert outcome is ExecutionOutcome.SUCCEEDED
    assert error_code is None


def test_confirmed_failure_maps_directly() -> None:
    outcome, error_code = classify_outcome(_result("FAILED"), None)
    assert outcome is ExecutionOutcome.FAILED
    assert error_code is None


def test_timeout_after_send_is_unknown() -> None:
    outcome, error_code = classify_outcome(None, TimeoutError())
    assert outcome is ExecutionOutcome.UNKNOWN
    assert error_code == "adapter_timeout_after_send"


def test_pre_send_rejection_is_failed_not_unknown() -> None:
    outcome, error_code = classify_outcome(None, AdapterPreSendError("bad parameters"))
    assert outcome is ExecutionOutcome.FAILED
    assert error_code == "adapter_pre_send_rejected"


def test_unexpected_adapter_error_is_unknown() -> None:
    outcome, error_code = classify_outcome(None, RuntimeError("connection refused"))
    assert outcome is ExecutionOutcome.UNKNOWN
    assert error_code == "adapter_unavailable"


def test_missing_result_is_unknown() -> None:
    outcome, _ = classify_outcome(None, None)
    assert outcome is ExecutionOutcome.UNKNOWN
