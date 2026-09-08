"""Concurrent execution-claim integration tests: at-most-one permit."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.adapters.base import TargetSnapshot
from hitl_ops.application.revalidation import RevalidationService
from hitl_ops.domain.enums import IntentState
from hitl_ops.domain.errors import StateConflictError
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.orm import ActionIntentORM, ExecutionORM
from tests.integration.conftest import create_approved_intent

_RESTART = {"environment": "staging", "service": "payments-api", "strategy": "rolling"}


class FakeTargetQuery:
    def __init__(self, snapshot: TargetSnapshot) -> None:
        self._snapshot = snapshot

    async def fetch(self, tool: object, parameters: dict) -> TargetSnapshot:
        service = parameters.get("service")
        identity = (
            {"service": service} if service else {"resource_id": parameters.get("resource_id")}
        )
        return TargetSnapshot(
            found=self._snapshot.found, identity=identity, health=self._snapshot.health
        )


def _healthy() -> FakeTargetQuery:
    return FakeTargetQuery(TargetSnapshot(found=True, health="healthy"))


async def _claim(session, pending: dict, worker: str, **kwargs: object):
    return await RevalidationService(session).claim_and_revalidate(
        tenant_id="tenant-1",
        intent_id=pending["intent_id"],
        revision=1,
        worker_id=worker,
        command_id=uuid.uuid4().hex,
        target_query=kwargs.get("target_query", _healthy()),
        bundle=kwargs["bundle"],
        lease_seconds=kwargs.get("lease_seconds", 60),
    )


async def test_two_concurrent_claims_yield_one_permit(
    migrated_database: str, engine: AsyncEngine
) -> None:
    from hitl_ops.domain.policy import (
        SEED_POLICY_BUNDLE_RULES,
        SEED_POLICY_BUNDLE_VERSION,
        PolicyBundle,
    )

    bundle = PolicyBundle(version=SEED_POLICY_BUNDLE_VERSION, rules=SEED_POLICY_BUNDLE_RULES)
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="restart_service", parameters=_RESTART)

    async with maker() as first, first.begin():
        permit = await _claim(first, pending, "worker-1", bundle=bundle)
        assert permit.intent_digest == pending["digest"]
        assert permit.precondition_token
        async with maker() as second:
            with pytest.raises(StateConflictError):
                await _claim(second, pending, "worker-2", bundle=bundle)

    async with maker() as session, session.begin():
        count = (
            await session.execute(
                select(func.count())
                .select_from(ExecutionORM)
                .where(ExecutionORM.intent_id == pending["intent_id"])
            )
        ).scalar_one()
        assert count == 1


async def test_claim_requires_executable_state(migrated_database: str, engine: AsyncEngine) -> None:
    from hitl_ops.domain.policy import (
        SEED_POLICY_BUNDLE_RULES,
        SEED_POLICY_BUNDLE_VERSION,
        PolicyBundle,
    )

    bundle = PolicyBundle(version=SEED_POLICY_BUNDLE_VERSION, rules=SEED_POLICY_BUNDLE_RULES)
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="restart_service", parameters=_RESTART)
    # Reject the pending approval so the intent is terminal.
    async with maker() as session, session.begin():
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
        assert intent is not None
        intent.state = IntentState.REJECTED.value
    async with maker() as session, session.begin():
        with pytest.raises(StateConflictError):
            await _claim(session, pending, "worker-1", bundle=bundle)


async def test_expired_claim_lease_is_retaken_not_duplicated(
    migrated_database: str, engine: AsyncEngine
) -> None:
    from hitl_ops.domain.policy import (
        SEED_POLICY_BUNDLE_RULES,
        SEED_POLICY_BUNDLE_VERSION,
        PolicyBundle,
    )

    bundle = PolicyBundle(version=SEED_POLICY_BUNDLE_VERSION, rules=SEED_POLICY_BUNDLE_RULES)
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="restart_service", parameters=_RESTART)
        # Simulate a worker that claimed and crashed: a stale CLAIMED row exists.
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
        permit = await _claim(session, pending, "worker-2", bundle=bundle)
    assert permit.operation_key == f"tenant-1:{pending['intent_id']}:1"

    async with maker() as session, session.begin():
        rows = (
            (
                await session.execute(
                    select(ExecutionORM).where(ExecutionORM.intent_id == pending["intent_id"])
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].attempt == 2
        assert rows[0].status == "EXECUTING"


async def test_one_byte_material_change_stales_the_claim(
    migrated_database: str, engine: AsyncEngine
) -> None:
    from hitl_ops.domain.policy import (
        SEED_POLICY_BUNDLE_RULES,
        SEED_POLICY_BUNDLE_VERSION,
        PolicyBundle,
    )

    bundle = PolicyBundle(version=SEED_POLICY_BUNDLE_VERSION, rules=SEED_POLICY_BUNDLE_RULES)
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="restart_service", parameters=_RESTART)
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
        assert intent is not None
        # A one-byte material change after approval.
        intent.canonical_parameters = {
            "environment": "staging",
            "service": "payments-apj",
            "strategy": "rolling",
        }

    async with maker() as session, session.begin():
        failure = await _claim(session, pending, "worker-1", bundle=bundle)
    assert failure is not None
    assert failure.state is IntentState.STALE
    assert failure.reason_code == "digest_mismatch"


async def test_live_target_gates_stale_the_claim(
    migrated_database: str, engine: AsyncEngine
) -> None:
    from hitl_ops.domain.policy import (
        SEED_POLICY_BUNDLE_RULES,
        SEED_POLICY_BUNDLE_VERSION,
        PolicyBundle,
    )

    bundle = PolicyBundle(version=SEED_POLICY_BUNDLE_VERSION, rules=SEED_POLICY_BUNDLE_RULES)
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="restart_service", parameters=_RESTART)

    async with maker() as session, session.begin():
        failure = await _claim(
            session,
            pending,
            "worker-1",
            bundle=bundle,
            target_query=FakeTargetQuery(TargetSnapshot(found=True, health="degraded")),
        )
    assert failure is not None
    assert failure.reason_code == "target_degraded"
