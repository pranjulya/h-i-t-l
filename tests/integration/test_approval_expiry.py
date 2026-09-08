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
    return AuthenticatedActor(actor_id=actor_id, tenant_id="tenant-1", correlation_id="corr-1")


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

    async with maker() as session:
        try:
            await ApprovalCommandService(session).decide(_command(pending))
            await session.commit()
            pytest.fail("expected ApprovalExpiredError")
        except ApprovalExpiredError:
            await session.commit()

    async with maker() as session, session.begin():
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
        assert intent is not None
        assert intent.state == IntentState.EXPIRED.value
        assert intent.state_version == pending["expected_state_version"] + 1


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
