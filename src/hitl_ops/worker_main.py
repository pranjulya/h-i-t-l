"""Standalone worker process for the deployed composition.

Runs bounded ticks in a loop against the configured database and demo adapter,
so the deployed stack can execute and reconcile intents without the API
process. This is the learning-mode scheduler; a real deployment replaces the
in-memory adapter and adds a durable scheduler.

A tick is supervised: an unexpected failure is logged and retried with
exponential backoff rather than terminating the process, because a worker
that exits on the first transient database error stops all execution until
something restarts it. Backoff resets after a successful tick.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

from hitl_ops.adapters.demo import DemoInfrastructureAdapter
from hitl_ops.config import Settings
from hitl_ops.infrastructure.database import build_engine, build_sessionmaker
from hitl_ops.observability.logging import configure_logging
from hitl_ops.worker import run_worker_tick

logger = logging.getLogger(__name__)

IDLE_SECONDS = float(os.environ.get("WORKER_TICK_INTERVAL_SECONDS", "5"))
MAX_BACKOFF_SECONDS = float(os.environ.get("WORKER_MAX_BACKOFF_SECONDS", "60"))

# The backoff is capped long before this, but the exponent must be bounded too:
# it is evaluated inside the failure handler, so an overflow here would kill
# the supervisor that exists to survive failures.
_MAX_BACKOFF_EXPONENT = 20


def next_backoff_seconds(failures: int, *, idle_seconds: float = IDLE_SECONDS) -> float:
    """Exponential backoff for consecutive tick failures, capped."""

    if failures < 1:
        return idle_seconds
    exponent = min(failures - 1, _MAX_BACKOFF_EXPONENT)
    backoff: float = idle_seconds * 2**exponent
    return min(backoff, MAX_BACKOFF_SECONDS)


async def supervise(
    tick: Callable[[], Awaitable[object]],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    idle_seconds: float = IDLE_SECONDS,
) -> None:
    """Run ticks forever, backing off on failure instead of exiting."""

    consecutive_failures = 0
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            consecutive_failures += 1
            delay = next_backoff_seconds(consecutive_failures, idle_seconds=idle_seconds)
            logger.exception(
                "worker tick failed (%s consecutive); retrying in %.1fs",
                consecutive_failures,
                delay,
            )
            await sleep(delay)
            continue
        consecutive_failures = 0
        await sleep(idle_seconds)


async def _run() -> None:
    settings = Settings()
    configure_logging(settings.log_level, settings.app_env.value)
    engine = build_engine(settings.database_url)
    maker = build_sessionmaker(engine)
    adapter = DemoInfrastructureAdapter()
    try:
        await supervise(lambda: run_worker_tick(maker, adapter))
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
