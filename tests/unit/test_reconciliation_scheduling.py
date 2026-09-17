"""Reconciliation backoff schedule: bounded, exponential, capped."""

from __future__ import annotations

from hitl_ops.application.reconciliation import (
    RECONCILE_BACKOFF_BASE_SECONDS,
    RECONCILE_BACKOFF_CEILING_SECONDS,
    reconcile_backoff_seconds,
)


def test_backoff_doubles_with_each_attempt() -> None:
    base = RECONCILE_BACKOFF_BASE_SECONDS
    assert reconcile_backoff_seconds(1) == base
    assert reconcile_backoff_seconds(2) == base * 2
    assert reconcile_backoff_seconds(3) == base * 4
    assert reconcile_backoff_seconds(4) == base * 8


def test_backoff_is_capped() -> None:
    assert reconcile_backoff_seconds(50) == RECONCILE_BACKOFF_CEILING_SECONDS


def test_backoff_of_a_nonsensical_attempt_count_falls_back_to_the_base() -> None:
    assert reconcile_backoff_seconds(0) == RECONCILE_BACKOFF_BASE_SECONDS
