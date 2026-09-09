"""Approval expiry integration tests: server/DB clock decides."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.application.commands import ApprovalCommandService, ApprovalDecisionCommand
from hitl_ops.domain.enums import ApprovalDecision, IntentState
from hitl_ops.domain.errors import ApprovalExpiredError
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.identity import AuthenticatedActor
from hitl_ops.infrastructure.orm import ActionIntentORM
from tests.integration.conftest import create_pending_intent, grant_role_directly

_RESTART = {"environment": "staging", "service": "payments-api", "strategy": "rolling"}


def _actor(actor_id: str) -> AuthenticatedActor:
    return AuthenticatedActor(
        actor_id=actor_id,
        tenant_id="tenant-1",
        correlation_id="corr-1",
        scopes=frozenset({"ops:write", "ops:read"}),
    )


def _command(pending: dict) -> ApprovalDecisionCommand:
    return ApprovalDecisionCommand(
        tenant_id="tenant-1",
        intent_id=pending["intent_id"],
        revision=1,
        intent_digest=pending["digest"],
        level=1,
        decision=ApprovalDecision.APPROVE,
        reason="approve in time",
        expected_state_version=pending["expected_state_version"],
        actor=_actor("approver-1"),
        command_id=uuid.uuid4().hex,
        obligations={"announce_in_incident_channel": "inc-123"},
    )


async def test_decision_on_expired_window_moves_to_expired(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "approver-1", "approver")
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
        assert intent is not None
        intent.approval_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    # Caller commits on the expiry path so the EXPIRED transition persists;
    # rolling back here would discard the durable expiry the error reports.
    async with maker() as session, session.begin():
        with pytest.raises(ApprovalExpiredError):
            await ApprovalCommandService(session).decide(_command(pending))

    async with maker() as session, session.begin():
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
        assert intent is not None
        assert intent.state == IntentState.EXPIRED.value
        assert intent.state_version == pending["expected_state_version"] + 1

    # The retried command observes the durable EXPIRED state, not a replay of
    # the expiry error path: still EXPIRED, still +1 version, no new transition.
    # The retry carries the pre-expiry expected version, so the version guard
    # rejects it before any new transition row can be written.
    from hitl_ops.domain.errors import StateConflictError

    async with maker() as session, session.begin():
        from sqlalchemy import func, select

        from hitl_ops.infrastructure.orm import StateTransitionORM

        before = (
            await session.execute(
                select(func.count())
                .select_from(StateTransitionORM)
                .where(StateTransitionORM.intent_id == pending["intent_id"])
            )
        ).scalar_one()

    async with maker() as session, session.begin():
        with pytest.raises(StateConflictError):
            await ApprovalCommandService(session).decide(_command(pending))

    async with maker() as session, session.begin():
        from sqlalchemy import func, select

        from hitl_ops.infrastructure.orm import StateTransitionORM

        after = (
            await session.execute(
                select(func.count())
                .select_from(StateTransitionORM)
                .where(StateTransitionORM.intent_id == pending["intent_id"])
            )
        ).scalar_one()
        assert after == before


async def test_decision_within_window_succeeds(migrated_database: str, engine: AsyncEngine) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "approver-1", "approver")
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
        assert intent is not None
        intent.approval_expires_at = datetime.now(UTC) + timedelta(minutes=10)

    async with maker() as session, session.begin():
        snapshot = await ApprovalCommandService(session).decide(_command(pending))
    assert snapshot.state is IntentState.APPROVED
