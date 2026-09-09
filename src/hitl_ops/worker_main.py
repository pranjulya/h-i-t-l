"""Standalone worker process for the deployed composition.

Runs bounded ticks in a loop against the configured database and demo adapter,
so the deployed stack can execute and reconcile intents without the API
process. This is the learning-mode scheduler; a real deployment replaces the
in-memory adapter and adds a durable scheduler.
"""

from __future__ import annotations

import asyncio
import os

from hitl_ops.adapters.demo import DemoInfrastructureAdapter
from hitl_ops.config import Settings
from hitl_ops.infrastructure.database import build_engine, build_sessionmaker
from hitl_ops.observability.logging import configure_logging
from hitl_ops.worker import run_worker_tick

_TICK_INTERVAL_SECONDS = float(os.environ.get("WORKER_TICK_INTERVAL_SECONDS", "5"))


async def _run() -> None:
    settings = Settings()
    configure_logging(settings.log_level, settings.app_env.value)
    engine = build_engine(settings.database_url)
    maker = build_sessionmaker(engine)
    adapter = DemoInfrastructureAdapter()
    try:
        while True:
            await run_worker_tick(maker, adapter)
            await asyncio.sleep(_TICK_INTERVAL_SECONDS)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
