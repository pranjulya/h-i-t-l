"""Worker supervision: an unexpected tick failure must not end the process."""

from __future__ import annotations

import asyncio

import pytest

from hitl_ops.worker_main import MAX_BACKOFF_SECONDS, next_backoff_seconds, supervise

_IDLE = 5.0


class _Stop(BaseException):
    """Sentinel that escapes the supervisor's Exception handler."""


def test_backoff_doubles_then_caps() -> None:
    assert next_backoff_seconds(0, idle_seconds=_IDLE) == _IDLE
    assert next_backoff_seconds(1, idle_seconds=_IDLE) == _IDLE
    assert next_backoff_seconds(2, idle_seconds=_IDLE) == _IDLE * 2
    assert next_backoff_seconds(3, idle_seconds=_IDLE) == _IDLE * 4
    assert next_backoff_seconds(50, idle_seconds=_IDLE) == MAX_BACKOFF_SECONDS


def test_backoff_does_not_overflow_on_a_long_outage() -> None:
    # The exponent is evaluated inside the failure handler, so an OverflowError
    # here would terminate the supervisor instead of backing off.
    assert next_backoff_seconds(10**6, idle_seconds=_IDLE) == MAX_BACKOFF_SECONDS
    assert next_backoff_seconds(2000, idle_seconds=_IDLE) == MAX_BACKOFF_SECONDS


async def test_the_supervisor_survives_a_very_long_failure_streak() -> None:
    delays: list[float] = []

    async def tick() -> None:
        raise RuntimeError("still down")

    async def sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) == 1500:
            raise _Stop()

    with pytest.raises(_Stop):
        await supervise(tick, sleep=sleep, idle_seconds=_IDLE)

    assert delays[-1] == MAX_BACKOFF_SECONDS
    assert len(delays) == 1500


async def test_a_failing_tick_is_retried_with_backoff_instead_of_exiting() -> None:
    calls: list[int] = []
    delays: list[float] = []

    async def tick() -> None:
        calls.append(len(calls) + 1)
        if len(calls) <= 2:
            raise RuntimeError("transient database error")
        raise _Stop()

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    with pytest.raises(_Stop):
        await supervise(tick, sleep=sleep, idle_seconds=_IDLE)

    assert len(calls) == 3  # it kept going after two consecutive failures
    assert delays == [_IDLE, _IDLE * 2]  # backoff before each retry


async def test_backoff_resets_after_a_successful_tick() -> None:
    delays: list[float] = []
    script: list[object] = [RuntimeError, RuntimeError, None, RuntimeError, _Stop]

    async def tick() -> None:
        step = script.pop(0)
        if step is _Stop:
            raise _Stop()
        if step is not None:
            raise step("boom")  # type: ignore[misc]

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    with pytest.raises(_Stop):
        await supervise(tick, sleep=sleep, idle_seconds=_IDLE)

    # two failures back off to 2x, then a success resets the counter, so the
    # next failure backs off to 1x again rather than 3x.
    assert delays == [_IDLE, _IDLE * 2, _IDLE, _IDLE]


async def test_cancellation_is_not_swallowed() -> None:
    async def tick() -> None:
        raise asyncio.CancelledError

    async def sleep(_seconds: float) -> None:  # pragma: no cover - never reached
        return None

    with pytest.raises(asyncio.CancelledError):
        await supervise(tick, sleep=sleep, idle_seconds=_IDLE)
