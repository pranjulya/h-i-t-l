"""Hash-chain audit unit tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from hitl_ops.infrastructure.audit import compute_event_hash

_AGGREGATE_ID = uuid.uuid4()


def _kwargs(**overrides: object) -> dict:
    values: dict = {
        "tenant_id": "tenant-1",
        "aggregate_id": _AGGREGATE_ID,
        "aggregate_revision": 1,
        "sequence": 1,
        "event_type": "intent_created",
        "actor_id": "user-1",
        "occurred_at": datetime(2026, 9, 9, tzinfo=UTC),
        "correlation_id": "corr-1",
        "causation_id": "cmd-1",
        "policy_version": "policy-1",
        "risk_version": "risk-rules-1",
        "attributes": {"digest": "a" * 64},
        "previous_hash": "0" * 64,
    }
    values.update(overrides)
    return values


def test_hash_is_deterministic_for_identical_bodies() -> None:
    assert compute_event_hash(**_kwargs()) == compute_event_hash(**_kwargs())


def test_any_field_change_changes_the_hash() -> None:
    base = compute_event_hash(**_kwargs())
    assert base != compute_event_hash(**_kwargs(sequence=2))
    assert base != compute_event_hash(**_kwargs(actor_id="user-2"))
    assert base != compute_event_hash(**_kwargs(previous_hash="1" * 64))
    assert base != compute_event_hash(**_kwargs(attributes={"digest": "b" * 64}))


def test_chain_links_bind_to_previous_hash() -> None:
    first = compute_event_hash(**_kwargs())
    second = compute_event_hash(**_kwargs(sequence=2, previous_hash=first))
    third = compute_event_hash(**_kwargs(sequence=3, previous_hash=second))
    assert first != second != third
    assert third.startswith("")


def test_occurred_at_is_absolute_and_timezone_bound() -> None:
    aware = compute_event_hash(**_kwargs(occurred_at=datetime(2026, 9, 9, tzinfo=UTC)))
    naive_same_wallclock = compute_event_hash(**_kwargs(occurred_at=datetime(2026, 9, 9)))
    assert aware != naive_same_wallclock
