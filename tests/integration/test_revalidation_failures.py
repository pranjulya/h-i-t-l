"""Revalidation failure handling: no target-lookup failure may strand a claim.

The phase-1 claim is committed before the target read, so an unhandled lookup
failure used to leave the intent in REVALIDATING, where no worker pass picks it
up. Every failure must now produce a durable, terminal outcome.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.adapters.base import TargetSnapshot
from hitl_ops.application.revalidation import (
    ExecutionPermit,
    RevalidationFailure,
    RevalidationService,
)
from hitl_ops.domain.enums import IntentState
from hitl_ops.domain.errors import StateConflictError
from hitl_ops.domain.policy import SEED_POLICY_BUNDLE_RULES, PolicyBundle
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.orm import ActionIntentORM, ExecutionORM
from tests.integration.conftest import create_approved_intent

_SCALE = {"environment": "staging", "service": "api", "replicas": 4}


def _bundle() -> PolicyBundle:
    return PolicyBundle(version="policy-1", rules=SEED_POLICY_BUNDLE_RULES)


class FailingTarget:
    """Raises a caller-supplied error on every fetch."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def fetch(self, tool: object, parameters: dict) -> TargetSnapshot:
        raise self._error


class FlakyTarget:
    """Fails the first call, then behaves like a healthy platform."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls = 0

    async def fetch(self, tool: object, parameters: dict) -> TargetSnapshot:
        self.calls += 1
        if self.calls == 1:
            raise self._error
        return TargetSnapshot(found=True, health="healthy", identity={"service": "api"})


async def _claim(session, pending: dict, target_query: object):
    return await RevalidationService(session).claim_and_revalidate(
        tenant_id="tenant-1",
        intent_id=pending["intent_id"],
        revision=1,
        worker_id="worker-1",
        command_id=uuid.uuid4().hex,
        target_query=target_query,  # type: ignore[arg-type]
        bundle=_bundle(),
    )


async def _intent_and_execution(engine: AsyncEngine, intent_id: uuid.UUID):
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        intent = await session.get(ActionIntentORM, ("tenant-1", intent_id, 1))
        execution = (
            await session.execute(select(ExecutionORM).where(ExecutionORM.intent_id == intent_id))
        ).scalar_one_or_none()
    return intent, execution


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        (RuntimeError("provider lookup exploded"), "target_fetch_failed"),
        (TimeoutError(), "target_fetch_timeout"),
        (ValueError("malformed platform response"), "target_fetch_failed"),
    ],
)
async def test_any_lookup_failure_fails_closed_without_stranding(
    migrated_database: str, engine: AsyncEngine, error: Exception, expected_reason: str
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="scale_service", parameters=_SCALE)

    async with maker() as session:
        result = await _claim(session, pending, FailingTarget(error))

    assert isinstance(result, RevalidationFailure)
    assert result.state is IntentState.STALE
    assert result.reason_code == expected_reason

    intent, execution = await _intent_and_execution(engine, pending["intent_id"])
    assert intent is not None
    # The decisive assertion: never left in REVALIDATING with a committed claim.
    assert intent.state == IntentState.STALE.value
    assert execution is not None
    assert execution.status == "FAILED"
    assert execution.error_code == expected_reason

    # A later worker pass refuses the stale revision rather than re-claiming it.
    async with maker() as session:
        with pytest.raises(StateConflictError):
            await _claim(session, pending, FailingTarget(error))


async def test_transient_lookup_failure_is_retried_within_the_claim(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="scale_service", parameters=_SCALE)

    target = FlakyTarget(RuntimeError("transient blip"))
    async with maker() as session:
        result = await _claim(session, pending, target)

    assert target.calls == 2, "the target read should be retried once"
    assert isinstance(result, ExecutionPermit)

    intent, execution = await _intent_and_execution(engine, pending["intent_id"])
    assert intent is not None
    assert intent.state == IntentState.EXECUTING.value
    assert execution is not None
    assert execution.status == "EXECUTING"
