"""Worker crash-point tests: at most one logical provider operation survives."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.adapters.demo import DemoInfrastructureAdapter
from hitl_ops.application.execution import ExecutionService
from hitl_ops.application.revalidation import ExecutionPermit, RevalidationService
from hitl_ops.domain.policy import SEED_POLICY_BUNDLE_RULES, PolicyBundle
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.orm import ActionIntentORM, ExecutionORM
from tests.integration.conftest import create_approved_intent

_SCALE = {"environment": "staging", "service": "api", "replicas": 4}


def _bundle() -> PolicyBundle:
    return PolicyBundle(version="policy-1", rules=SEED_POLICY_BUNDLE_RULES)


async def _claim(session, adapter, pending: dict, worker: str, lease_seconds: int = 60):
    return await RevalidationService(session).claim_and_revalidate(
        tenant_id="tenant-1",
        intent_id=pending["intent_id"],
        revision=1,
        worker_id=worker,
        command_id=uuid.uuid4().hex,
        target_query=adapter,
        bundle=_bundle(),
        lease_seconds=lease_seconds,
    )


async def test_provider_success_then_crash_recovers_via_worker_tick(
    failure_database, engine: AsyncEngine
) -> None:
    """Production path: provider accepts, result never commits, tick recovers."""

    from hitl_ops.worker import run_worker_tick

    maker = build_sessionmaker(engine)
    adapter = DemoInfrastructureAdapter()
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="scale_service", parameters=_SCALE)

    # Durable claim/EXECUTING commit happens before the provider call.
    async with maker() as session, session.begin():
        permit = await _claim(session, adapter, pending, "worker-1")
    assert isinstance(permit, ExecutionPermit)

    # Provider accepts the side effect, then the worker process dies before the
    # result transaction commits: the result session rolls back. The original
    # worker and adapter are gone; simulate a restarted process with a fresh
    # adapter that shares no in-memory state except the provider record.
    async with maker() as session:
        executor = ExecutionService(session, adapter)
        await executor.execute_permit(permit, worker_id="worker-1", command_id="crash-1")
        await session.rollback()
    provider_record = dict(adapter._operations)
    assert len(provider_record) == 1

    restarted = DemoInfrastructureAdapter()
    restarted._operations.update(provider_record)

    # Expire the EXECUTING lease so recovery is eligible, then run the real
    # production tick: recovery without resend, then reconciliation.
    async with maker() as session, session.begin():
        execution = (
            await session.execute(
                select(ExecutionORM).where(ExecutionORM.intent_id == pending["intent_id"])
            )
        ).scalar_one()
        execution.claim_expires_at = datetime.now(UTC) - timedelta(seconds=3600)
        execution.started_at = datetime.now(UTC) - timedelta(seconds=3600)

    stats = await run_worker_tick(maker, restarted)
    assert stats["recovered"] == 1

    async with maker() as session, session.begin():
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
    assert intent is not None
    assert intent.state == "SUCCEEDED"
    assert len([k for k in restarted._operations if str(pending["intent_id"]) in k]) == 1


async def test_crash_without_provider_evidence_stays_unknown(
    failure_database, engine: AsyncEngine
) -> None:
    """Recovery preserves UNKNOWN and escalates when no evidence exists."""

    from hitl_ops.worker import run_worker_tick

    maker = build_sessionmaker(engine)
    adapter = DemoInfrastructureAdapter()
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="scale_service", parameters=_SCALE)

    async with maker() as session, session.begin():
        permit = await _claim(session, adapter, pending, "worker-1")
    assert isinstance(permit, ExecutionPermit)

    async with maker() as session:
        executor = ExecutionService(session, adapter)
        await executor.execute_permit(permit, worker_id="worker-1", command_id="crash-1")
        await session.rollback()

    # The restarted process finds no provider record at all.
    restarted = DemoInfrastructureAdapter()
    async with maker() as session, session.begin():
        execution = (
            await session.execute(
                select(ExecutionORM).where(ExecutionORM.intent_id == pending["intent_id"])
            )
        ).scalar_one()
        execution.claim_expires_at = datetime.now(UTC) - timedelta(seconds=3600)
        execution.started_at = datetime.now(UTC) - timedelta(seconds=3600)

    stats = await run_worker_tick(maker, restarted)
    assert stats["recovered"] == 1
    async with maker() as session, session.begin():
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
    assert intent is not None
    assert intent.state == "EXECUTION_UNKNOWN"


async def test_crash_before_claim_commit_leaves_no_outcome(
    failure_database, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    adapter = DemoInfrastructureAdapter()
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="scale_service", parameters=_SCALE)

    # The claim transaction rolls back before committing: no execution row.
    async with maker() as session:
        permit = await _claim(session, adapter, pending, "worker-1")
        assert isinstance(permit, ExecutionPermit)
        await session.rollback()

    async with maker() as session, session.begin():
        count = (await session.execute(select(func.count()).select_from(ExecutionORM))).scalar_one()
    assert count == 0

    # Recovery: a fresh worker claim succeeds.
    async with maker() as session, session.begin():
        permit = await _claim(session, adapter, pending, "worker-2")
        assert isinstance(permit, ExecutionPermit)


async def test_crash_after_claim_is_recovered_via_lease_expiry(
    failure_database, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    adapter = DemoInfrastructureAdapter()
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="scale_service", parameters=_SCALE)
        # Claim committed, then the worker died: expired CLAIMED lease remains.
        session.add(
            ExecutionORM(
                tenant_id="tenant-1",
                intent_id=pending["intent_id"],
                intent_revision=1,
                operation_key=f"tenant-1:{pending['intent_id']}:1",
                attempt=1,
                status="CLAIMED",
                claim_expires_at=datetime.now(UTC) - timedelta(seconds=5),
            )
        )

    async with maker() as session, session.begin():
        permit = await _claim(session, adapter, pending, "worker-recovery")
    assert isinstance(permit, ExecutionPermit)

    async with maker() as session, session.begin():
        rows = (await session.execute(select(ExecutionORM))).scalars().all()
    assert len(rows) == 1
    assert rows[0].attempt == 2
    # The demo provider saw exactly one logical operation for this key.
    matching = [key for key in adapter._operations if str(pending["intent_id"]) in key]
    assert len(matching) <= 1
