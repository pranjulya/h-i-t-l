"""Reconciliation integration tests: evidence decides, unknown is preserved."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.adapters.demo import DemoInfrastructureAdapter
from hitl_ops.application.reconciliation import ReconciliationService
from hitl_ops.domain.enums import ExecutionOutcome, IntentState
from hitl_ops.domain.errors import StateConflictError
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.orm import ActionIntentORM, ExecutionORM
from hitl_ops.worker import run_worker_tick
from tests.integration.conftest import create_approved_intent

_TIMEOUT_RESTART = {
    "environment": "staging",
    "service": "timeout-api",
    "strategy": "rolling",
}


async def _unknown_execution(engine: AsyncEngine) -> tuple[uuid.UUID, DemoInfrastructureAdapter]:
    maker = build_sessionmaker(engine)
    adapter = DemoInfrastructureAdapter()
    async with maker() as session, session.begin():
        pending = await create_approved_intent(
            session, tool="restart_service", parameters=_TIMEOUT_RESTART
        )
    await run_worker_tick(maker, adapter)
    async with maker() as session, session.begin():
        execution = (
            await session.execute(
                select(ExecutionORM).where(ExecutionORM.intent_id == pending["intent_id"])
            )
        ).scalar_one()
        execution_id = execution.id
        assert execution.status == "UNKNOWN"
    return execution_id, adapter


async def test_provider_evidence_maps_unknown_to_success(
    migrated_database: str, engine: AsyncEngine
) -> None:
    execution_id, adapter = await _unknown_execution(engine)
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        result = await ReconciliationService(session, adapter).reconcile(
            execution_id=execution_id, worker_id="worker-r", command_id=uuid.uuid4().hex
        )
    assert result.outcome is ExecutionOutcome.SUCCEEDED
    assert result.state is IntentState.SUCCEEDED
    assert result.escalated is False


async def test_missing_evidence_preserves_unknown_and_escalates(
    migrated_database: str, engine: AsyncEngine
) -> None:
    execution_id, adapter = await _unknown_execution(engine)
    # Simulate a provider with no record of the operation.
    adapter._operations.clear()
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        result = await ReconciliationService(session, adapter).reconcile(
            execution_id=execution_id, worker_id="worker-r", command_id=uuid.uuid4().hex
        )
    assert result.outcome is ExecutionOutcome.UNKNOWN
    assert result.state is IntentState.EXECUTION_UNKNOWN
    assert result.escalated is True

    async with maker() as session, session.begin():
        execution = await session.get(ExecutionORM, execution_id)
        assert execution is not None
        assert execution.result_summary is not None
        assert execution.result_summary.get("escalated") is True


async def test_provider_outage_preserves_unknown(
    migrated_database: str, engine: AsyncEngine
) -> None:
    execution_id, _adapter = await _unknown_execution(engine)

    class UnavailableAdapter(DemoInfrastructureAdapter):
        async def lookup_status(self, tool, operation_key, provider_operation_id):
            raise RuntimeError("provider status unavailable")

    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        result = await ReconciliationService(session, UnavailableAdapter()).reconcile(
            execution_id=execution_id, worker_id="worker-r", command_id=uuid.uuid4().hex
        )
    assert result.outcome is ExecutionOutcome.UNKNOWN
    assert result.escalated is True


async def test_resolved_execution_cannot_reconcile_again(
    migrated_database: str, engine: AsyncEngine
) -> None:
    execution_id, adapter = await _unknown_execution(engine)
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await ReconciliationService(session, adapter).reconcile(
            execution_id=execution_id, worker_id="worker-r", command_id=uuid.uuid4().hex
        )
    async with maker() as session, session.begin():
        with pytest.raises(StateConflictError):
            await ReconciliationService(session, adapter).reconcile(
                execution_id=execution_id, worker_id="worker-r", command_id=uuid.uuid4().hex
            )


async def test_reconciled_intent_leaves_terminal_state_consistent(
    migrated_database: str, engine: AsyncEngine
) -> None:
    execution_id, adapter = await _unknown_execution(engine)
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        result = await ReconciliationService(session, adapter).reconcile(
            execution_id=execution_id, worker_id="worker-r", command_id=uuid.uuid4().hex
        )
    assert result.state is IntentState.SUCCEEDED
    async with maker() as session, session.begin():
        execution = await session.get(ExecutionORM, execution_id)
        assert execution is not None
        assert execution.status == "SUCCEEDED"
        intent = await session.get(ActionIntentORM, ("tenant-1", execution.intent_id, 1))
        assert intent is not None
        assert intent.state == IntentState.SUCCEEDED.value
