"""Reconciliation integration tests: evidence decides, unknown is preserved."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.adapters.demo import DemoInfrastructureAdapter
from hitl_ops.application.reconciliation import (
    MAX_RECONCILE_ATTEMPTS,
    ReconciliationService,
)
from hitl_ops.domain.enums import ExecutionOutcome, IntentState
from hitl_ops.domain.errors import StateConflictError
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.orm import ActionIntentORM, ExecutionORM, OutboxMessageORM
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


async def test_one_poisoned_reconciliation_does_not_abort_the_tick(
    monkeypatch: pytest.MonkeyPatch, migrated_database: str, engine: AsyncEngine
) -> None:
    """A non-domain failure must not starve the rest of the tick."""

    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        for _ in range(2):
            intent_id = uuid.uuid4()
            session.add(
                ActionIntentORM(
                    tenant_id="tenant-1",
                    intent_id=intent_id,
                    revision=1,
                    tool="scale_service",
                    canonical_parameters={"environment": "staging", "service": "api"},
                    intent_digest="e" * 64,
                    requester_id="user-1",
                    requester_rationale="r",
                    source="DIRECT",
                    state="EXECUTION_UNKNOWN",
                    state_version=3,
                )
            )
            session.add(
                ExecutionORM(
                    tenant_id="tenant-1",
                    intent_id=intent_id,
                    intent_revision=1,
                    operation_key=f"tenant-1:{intent_id}:1",
                    attempt=1,
                    status="UNKNOWN",
                )
            )

    genuine = ReconciliationService.reconcile
    calls = {"count": 0}

    async def flaky(self, **kwargs):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("poisoned execution")
        return await genuine(self, **kwargs)

    monkeypatch.setattr(ReconciliationService, "reconcile", flaky)

    stats = await run_worker_tick(maker, DemoInfrastructureAdapter())

    assert calls["count"] == 2  # the second execution was still attempted
    assert stats["skipped"] >= 1
    assert stats["reconciled"] >= 1


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


async def test_missing_evidence_schedules_a_backoff_instead_of_retrying_immediately(
    migrated_database: str, engine: AsyncEngine
) -> None:
    execution_id, adapter = await _unknown_execution(engine)
    adapter._operations.clear()
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await ReconciliationService(session, adapter).reconcile(
            execution_id=execution_id, worker_id="worker-r", command_id=uuid.uuid4().hex
        )

    async with maker() as session, session.begin():
        execution = await session.get(ExecutionORM, execution_id)
        assert execution is not None
        assert execution.reconcile_attempts == 1
        assert execution.next_reconcile_at is not None
        assert execution.next_reconcile_at > datetime.now(UTC)


async def test_worker_tick_skips_an_unknown_until_its_backoff_elapses(
    migrated_database: str, engine: AsyncEngine
) -> None:
    execution_id, adapter = await _unknown_execution(engine)
    adapter._operations.clear()
    maker = build_sessionmaker(engine)

    calls: list[str] = []

    class CountingAdapter(DemoInfrastructureAdapter):
        async def lookup_status(self, tool, operation_key, provider_operation_id):
            calls.append(operation_key)
            return await super().lookup_status(tool, operation_key, provider_operation_id)

    counting = CountingAdapter()
    counting._operations.clear()

    # Reconcile once so the attempt is scheduled into the future.
    async with maker() as session, session.begin():
        await ReconciliationService(session, adapter).reconcile(
            execution_id=execution_id, worker_id="worker-r", command_id=uuid.uuid4().hex
        )
    # Not due yet: the tick must not call the provider again.
    await run_worker_tick(maker, counting)
    assert calls == []

    # Once due, exactly one provider status call is made.
    async with maker() as session, session.begin():
        execution = await session.get(ExecutionORM, execution_id)
        assert execution is not None
        execution.next_reconcile_at = datetime.now(UTC) - timedelta(seconds=1)
    await run_worker_tick(maker, counting)
    assert len(calls) == 1


async def test_reconciliation_attempts_are_bounded_then_escalate_to_operators(
    migrated_database: str, engine: AsyncEngine
) -> None:
    execution_id, adapter = await _unknown_execution(engine)
    adapter._operations.clear()
    maker = build_sessionmaker(engine)

    async with maker() as session, session.begin():
        execution = await session.get(ExecutionORM, execution_id)
        assert execution is not None
        execution.reconcile_attempts = MAX_RECONCILE_ATTEMPTS - 1
        execution.next_reconcile_at = datetime.now(UTC) - timedelta(seconds=1)

    await run_worker_tick(maker, adapter)

    async with maker() as session, session.begin():
        execution = await session.get(ExecutionORM, execution_id)
        assert execution is not None
        assert execution.reconcile_attempts == MAX_RECONCILE_ATTEMPTS
        assert execution.next_reconcile_at is None
        assert execution.error_code == "reconcile_attempts_exhausted"
        topics = (
            (
                await session.execute(
                    select(OutboxMessageORM.topic).where(
                        OutboxMessageORM.payload["intent_id"].astext == str(execution.intent_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "execution.reconciliation_exhausted" in topics

    # Exhausted executions are never selected again.
    second = await run_worker_tick(maker, adapter)
    assert second["reconciled"] == 0
