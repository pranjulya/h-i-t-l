"""Execution worker integration tests."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.adapters.demo import DemoInfrastructureAdapter
from hitl_ops.domain.enums import IntentState
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.orm import ActionIntentORM, ExecutionORM, OutboxMessageORM
from hitl_ops.worker import run_worker_tick
from tests.integration.conftest import create_approved_intent

_SCALE = {"environment": "staging", "service": "api", "replicas": 4}


async def test_worker_executes_eligible_intent_end_to_end(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    adapter = DemoInfrastructureAdapter()
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="scale_service", parameters=_SCALE)

    stats = await run_worker_tick(maker, adapter)
    assert stats["executed"] == 1

    async with maker() as session, session.begin():
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
        assert intent is not None
        assert intent.state == IntentState.SUCCEEDED.value

        execution = (
            await session.execute(
                select(ExecutionORM).where(ExecutionORM.intent_id == pending["intent_id"])
            )
        ).scalar_one()
        assert execution.status == "SUCCEEDED"
        assert execution.provider_operation_id
        assert execution.precondition_snapshot is not None

        topics = (
            (
                await session.execute(
                    select(OutboxMessageORM.topic).where(
                        OutboxMessageORM.payload["intent_id"].astext == str(pending["intent_id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "execution.succeeded" in topics


async def test_worker_performs_at_most_one_provider_operation(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    adapter = DemoInfrastructureAdapter()
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="scale_service", parameters=_SCALE)

    await run_worker_tick(maker, adapter)
    await run_worker_tick(maker, adapter)  # nothing left eligible

    async with maker() as session, session.begin():
        executions = (
            await session.execute(
                select(func.count())
                .select_from(ExecutionORM)
                .where(ExecutionORM.intent_id == pending["intent_id"])
            )
        ).scalar_one()
        assert executions == 1
    provider_ops = (
        [
            key
            for key, record in adapter._operations.items()
            if record["intent"] == str(pending["intent_id"])
        ]
        if False
        else None
    )
    # The demo adapter records one operation per unique operation key.
    matching = [key for key in adapter._operations if str(pending["intent_id"]) in key]
    assert len(matching) == 1


async def test_worker_marks_confirmed_failure(migrated_database: str, engine: AsyncEngine) -> None:
    maker = build_sessionmaker(engine)
    adapter = DemoInfrastructureAdapter()
    async with maker() as session, session.begin():
        pending = await create_approved_intent(
            session,
            tool="restart_service",
            parameters={"environment": "staging", "service": "boom-api", "strategy": "rolling"},
        )

    await run_worker_tick(maker, adapter)
    async with maker() as session, session.begin():
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
        assert intent is not None
        assert intent.state == IntentState.FAILED.value
        execution = (
            await session.execute(
                select(ExecutionORM).where(ExecutionORM.intent_id == pending["intent_id"])
            )
        ).scalar_one()
        assert execution.status == "FAILED"
        assert execution.error_code == "DEMO_PROVIDER_FAILURE"


async def test_ambiguous_send_enters_execution_unknown(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    adapter = DemoInfrastructureAdapter()
    async with maker() as session, session.begin():
        pending = await create_approved_intent(
            session,
            tool="restart_service",
            parameters={"environment": "staging", "service": "timeout-api", "strategy": "rolling"},
        )

    await run_worker_tick(maker, adapter)
    async with maker() as session, session.begin():
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
        assert intent is not None
        assert intent.state == IntentState.EXECUTION_UNKNOWN.value
        execution = (
            await session.execute(
                select(ExecutionORM).where(ExecutionORM.intent_id == pending["intent_id"])
            )
        ).scalar_one()
        assert execution.status == "UNKNOWN"
        assert execution.provider_operation_id is not None
