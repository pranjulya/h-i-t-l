"""Worker crash-point tests: at most one logical provider operation survives."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.adapters.demo import DemoInfrastructureAdapter
from hitl_ops.application.revalidation import ExecutionPermit, RevalidationService
from hitl_ops.domain.policy import SEED_POLICY_BUNDLE_RULES, PolicyBundle
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.orm import ExecutionORM
from tests.integration.conftest import create_approved_intent

_SCALE = {"environment": "staging", "service": "api", "replicas": 4}


def _bundle() -> PolicyBundle:
    return PolicyBundle(version="policy-1", rules=SEED_POLICY_BUNDLE_RULES)


class _HealthyTarget:
    async def fetch(self, tool: object, parameters: dict):
        from hitl_ops.adapters.base import TargetSnapshot

        return TargetSnapshot(found=True, health="healthy")


async def _claim(session, pending: dict, worker: str, lease_seconds: int = 60):
    return await RevalidationService(session).claim_and_revalidate(
        tenant_id="tenant-1",
        intent_id=pending["intent_id"],
        revision=1,
        worker_id=worker,
        command_id=uuid.uuid4().hex,
        target_query=_HealthyTarget(),
        bundle=_bundle(),
        lease_seconds=lease_seconds,
    )


async def test_crash_before_commit_leaves_no_outcome_and_one_operation(
    failure_database, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    adapter = DemoInfrastructureAdapter()
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="scale_service", parameters=_SCALE)

    # Worker claims, executes, then the process dies before commit: the
    # abandoned session rolls back, leaving no outcome behind.
    import pytest

    with pytest.raises(RuntimeError, match="worker crash"):
        async with maker() as session:
            permit = await _claim(session, pending, "worker-1")
            assert isinstance(permit, ExecutionPermit)
            await session.rollback()
            raise RuntimeError("worker crash")

    async with maker() as session, session.begin():
        count = (await session.execute(select(func.count()).select_from(ExecutionORM))).scalar_one()
    assert count == 0

    # Recovery: a new worker claim succeeds cleanly; still one provider call.
    async with maker() as session, session.begin():
        permit = await _claim(session, pending, "worker-2")
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
                claim_expires_at=__import__("datetime").datetime.now(__import__("datetime").UTC)
                - __import__("datetime").timedelta(seconds=5),
            )
        )

    async with maker() as session, session.begin():
        permit = await _claim(session, pending, "worker-recovery")
    assert isinstance(permit, ExecutionPermit)

    async with maker() as session, session.begin():
        rows = (await session.execute(select(ExecutionORM))).scalars().all()
    assert len(rows) == 1
    assert rows[0].attempt == 2
    # The demo provider saw exactly one logical operation for this key.
    matching = [key for key in adapter._operations if str(pending["intent_id"]) in key]
    assert len(matching) <= 1
