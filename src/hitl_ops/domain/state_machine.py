"""The authoritative legal transition table for the intent lifecycle.

Only transitions listed here may be requested. Guard rules (distinctness,
expiry, authorization) are evaluated by the command layer; this module owns
which edges exist at all. No cancellation is accepted after the REVALIDATING
execution claim.
"""

from __future__ import annotations

from hitl_ops.domain.enums import IntentState
from hitl_ops.domain.errors import IllegalTransitionError

_LEGAL_TRANSITIONS: dict[IntentState, frozenset[IntentState]] = {
    IntentState.REQUESTED: frozenset({IntentState.RISK_EVALUATED, IntentState.CANCELLED}),
    IntentState.RISK_EVALUATED: frozenset({IntentState.POLICY_EVALUATED, IntentState.CANCELLED}),
    IntentState.POLICY_EVALUATED: frozenset(
        {
            IntentState.AUTO_APPROVED,
            IntentState.PENDING_APPROVAL_1,
            IntentState.BLOCKED,
            IntentState.CANCELLED,
        }
    ),
    IntentState.AUTO_APPROVED: frozenset({IntentState.REVALIDATING, IntentState.CANCELLED}),
    IntentState.PENDING_APPROVAL_1: frozenset(
        {
            IntentState.APPROVED,
            IntentState.APPROVED_BY_LEVEL_1,
            IntentState.REJECTED,
            IntentState.CANCELLED,
            IntentState.EXPIRED,
        }
    ),
    IntentState.APPROVED_BY_LEVEL_1: frozenset(
        {
            IntentState.PENDING_APPROVAL_2,
            IntentState.REJECTED,
            IntentState.CANCELLED,
            IntentState.EXPIRED,
        }
    ),
    IntentState.PENDING_APPROVAL_2: frozenset(
        {IntentState.APPROVED, IntentState.REJECTED, IntentState.CANCELLED, IntentState.EXPIRED}
    ),
    IntentState.APPROVED: frozenset(
        {IntentState.REVALIDATING, IntentState.CANCELLED, IntentState.EXPIRED}
    ),
    IntentState.REVALIDATING: frozenset(
        {IntentState.EXECUTING, IntentState.STALE, IntentState.EXPIRED}
    ),
    IntentState.EXECUTING: frozenset(
        {IntentState.SUCCEEDED, IntentState.FAILED, IntentState.EXECUTION_UNKNOWN}
    ),
    IntentState.EXECUTION_UNKNOWN: frozenset({IntentState.SUCCEEDED, IntentState.FAILED}),
    IntentState.SUCCEEDED: frozenset(),
    IntentState.FAILED: frozenset(),
    IntentState.BLOCKED: frozenset(),
    IntentState.REJECTED: frozenset(),
    IntentState.CANCELLED: frozenset(),
    IntentState.EXPIRED: frozenset(),
    IntentState.STALE: frozenset(),
}

CANCELLABLE_STATES: frozenset[IntentState] = frozenset(
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

EXPIRABLE_STATES: frozenset[IntentState] = frozenset(
    {
        IntentState.PENDING_APPROVAL_1,
        IntentState.APPROVED_BY_LEVEL_1,
        IntentState.PENDING_APPROVAL_2,
        IntentState.APPROVED,
    }
)


def is_legal(current: IntentState, target: IntentState) -> bool:
    return target in _LEGAL_TRANSITIONS.get(current, frozenset())


def require_transition(current: IntentState, target: IntentState) -> None:
    if not is_legal(current, target):
        raise IllegalTransitionError(f"transition {current.value} -> {target.value} is not legal")


def is_terminal(state: IntentState) -> bool:
    return not _LEGAL_TRANSITIONS.get(state, frozenset())
