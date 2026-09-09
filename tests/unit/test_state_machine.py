"""State machine authority unit tests: legal edges, cancellability, expiry."""

from __future__ import annotations

import pytest

from hitl_ops.domain.enums import IntentState
from hitl_ops.domain.errors import IllegalTransitionError
from hitl_ops.domain.state_machine import (
    CANCELLABLE_STATES,
    EXPIRABLE_STATES,
    is_legal,
    is_terminal,
    require_transition,
)


def test_every_state_has_a_transition_entry() -> None:
    from hitl_ops.domain import state_machine

    for state in IntentState:
        assert state in state_machine._LEGAL_TRANSITIONS


def test_main_lifecycle_edges_are_legal() -> None:
    assert is_legal(IntentState.REQUESTED, IntentState.RISK_EVALUATED)
    assert is_legal(IntentState.RISK_EVALUATED, IntentState.POLICY_EVALUATED)
    assert is_legal(IntentState.POLICY_EVALUATED, IntentState.AUTO_APPROVED)
    assert is_legal(IntentState.POLICY_EVALUATED, IntentState.PENDING_APPROVAL_1)
    assert is_legal(IntentState.POLICY_EVALUATED, IntentState.BLOCKED)
    assert is_legal(IntentState.PENDING_APPROVAL_1, IntentState.APPROVED)
    assert is_legal(IntentState.PENDING_APPROVAL_1, IntentState.APPROVED_BY_LEVEL_1)
    assert is_legal(IntentState.APPROVED_BY_LEVEL_1, IntentState.PENDING_APPROVAL_2)
    assert is_legal(IntentState.PENDING_APPROVAL_2, IntentState.APPROVED)
    assert is_legal(IntentState.AUTO_APPROVED, IntentState.REVALIDATING)
    assert is_legal(IntentState.APPROVED, IntentState.REVALIDATING)
    assert is_legal(IntentState.REVALIDATING, IntentState.EXECUTING)
    assert is_legal(IntentState.REVALIDATING, IntentState.STALE)
    assert is_legal(IntentState.EXECUTING, IntentState.EXECUTION_UNKNOWN)
    assert is_legal(IntentState.EXECUTION_UNKNOWN, IntentState.SUCCEEDED)
    assert is_legal(IntentState.EXECUTION_UNKNOWN, IntentState.FAILED)


def test_illegal_edges_are_rejected() -> None:
    for current, target in (
        (IntentState.REQUESTED, IntentState.APPROVED),
        (IntentState.REQUESTED, IntentState.EXECUTING),
        (IntentState.PENDING_APPROVAL_1, IntentState.PENDING_APPROVAL_2),
        (IntentState.APPROVED_BY_LEVEL_1, IntentState.APPROVED),
        (IntentState.APPROVED, IntentState.PENDING_APPROVAL_1),
        (IntentState.REJECTED, IntentState.APPROVED),
        (IntentState.SUCCEEDED, IntentState.FAILED),
        (IntentState.CANCELLED, IntentState.PENDING_APPROVAL_1),
        (IntentState.EXPIRED, IntentState.APPROVED),
        (IntentState.STALE, IntentState.REVALIDATING),
        (IntentState.EXECUTION_UNKNOWN, IntentState.EXECUTING),
    ):
        with pytest.raises(IllegalTransitionError):
            require_transition(current, target)


def test_cancellable_states_match_the_authoritative_document() -> None:
    assert (
        frozenset(
            {
                IntentState.REQUESTED,
                IntentState.RISK_EVALUATED,
                IntentState.POLICY_EVALUATED,
                IntentState.AUTO_APPROVED,
                IntentState.PENDING_APPROVAL_1,
                IntentState.APPROVED_BY_LEVEL_1,
                IntentState.PENDING_APPROVAL_2,
                IntentState.APPROVED,
            }
        )
        == CANCELLABLE_STATES
    )
    # No cancellation is accepted after the execution claim.
    assert IntentState.REVALIDATING not in CANCELLABLE_STATES
    assert not is_legal(IntentState.REVALIDATING, IntentState.CANCELLED)


def test_expirable_states_are_the_approval_windows() -> None:
    assert (
        frozenset(
            {
                IntentState.PENDING_APPROVAL_1,
                IntentState.APPROVED_BY_LEVEL_1,
                IntentState.PENDING_APPROVAL_2,
                IntentState.APPROVED,
            }
        )
        == EXPIRABLE_STATES
    )


def test_terminal_states_cannot_transition() -> None:
    for state in (
        IntentState.SUCCEEDED,
        IntentState.FAILED,
        IntentState.BLOCKED,
        IntentState.REJECTED,
        IntentState.CANCELLED,
        IntentState.EXPIRED,
        IntentState.STALE,
    ):
        assert is_terminal(state)
        for target in IntentState:
            if target is IntentState.EXECUTION_UNKNOWN:
                continue
            assert not is_legal(state, target)
