"""Scope authority: DB-backed scopes are enforced at approval and rechecked at execution.

Finding: scopes were checked when approving but never rechecked before
execution, so a scope removal was not caught the way a role revocation was.
Scopes are now current DB authority (ADR-008) evaluated at both points.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.adapters.base import TargetSnapshot
from hitl_ops.application.commands import (
    ApprovalCommandService,
    ApprovalDecisionCommand,
)
from hitl_ops.domain.enums import ApprovalDecision, IntentState
from hitl_ops.domain.errors import ForbiddenError
from hitl_ops.domain.policy import SEED_POLICY_BUNDLE_RULES, PolicyBundle
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.identity import AuthenticatedActor
from hitl_ops.infrastructure.orm import ActionIntentORM, RoleAssignmentORM
from tests.integration.conftest import create_pending_intent

_RESTART = {"environment": "staging", "service": "payments-api", "strategy": "rolling"}


def _actor(actor_id: str, scopes: frozenset[str] | None = None) -> AuthenticatedActor:
    return AuthenticatedActor(
        actor_id=actor_id,
        tenant_id="tenant-1",
        correlation_id="corr-1",
        scopes=scopes if scopes is not None else frozenset({"ops:read", "ops:write"}),
    )


def _bundle() -> PolicyBundle:
    return PolicyBundle(version="policy-1", rules=SEED_POLICY_BUNDLE_RULES)


class HealthyTarget:
    async def fetch(self, tool: object, parameters: dict) -> TargetSnapshot:
        return TargetSnapshot(
            found=True, identity={"service": parameters.get("service")}, health="healthy"
        )


async def _grant(engine: AsyncEngine, principal: str, role: str, scopes: list[str] | None) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        session.add(
            RoleAssignmentORM(
                tenant_id="tenant-1",
                principal_id=principal,
                role=role,
                environments=None,
                scopes=scopes,
                granted_by="test",
            )
        )


async def test_approval_requires_db_granted_scopes_even_when_token_asserts_them(
    migrated_database: str, engine: AsyncEngine
) -> None:
    """The token scope claim alone never grants authority (ADR-008)."""

    maker = build_sessionmaker(engine)
    await _grant(engine, "approver-1", "approver", scopes=None)  # role only, no scopes
    async with maker() as session, session.begin():
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)

    async with maker() as session, session.begin():
        with pytest.raises(ForbiddenError, match="scopes"):
            await ApprovalCommandService(session).decide(
                ApprovalDecisionCommand(
                    tenant_id="tenant-1",
                    intent_id=pending["intent_id"],
                    revision=1,
                    intent_digest=pending["digest"],
                    level=1,
                    decision=ApprovalDecision.APPROVE,
                    reason="token asserts scopes, DB does not",
                    expected_state_version=pending["expected_state_version"],
                    actor=_actor("approver-1", frozenset({"ops:read", "ops:write"})),
                    command_id=uuid.uuid4().hex,
                )
            )

    async with maker() as session, session.begin():
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
    assert intent is not None
    assert intent.state == IntentState.PENDING_APPROVAL_1.value


async def test_scope_removal_after_approval_blocks_execution(
    migrated_database: str, engine: AsyncEngine
) -> None:
    """The recheck that was missing: roles intact, scopes removed → STALE."""

    maker = build_sessionmaker(engine)
    await _grant(engine, "approver-1", "approver", scopes=["ops:read", "ops:write"])
    async with maker() as session, session.begin():
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)

    async with maker() as session, session.begin():
        await ApprovalCommandService(session).decide(
            ApprovalDecisionCommand(
                tenant_id="tenant-1",
                intent_id=pending["intent_id"],
                revision=1,
                intent_digest=pending["digest"],
                level=1,
                decision=ApprovalDecision.APPROVE,
                reason="approved with scopes",
                expected_state_version=pending["expected_state_version"],
                actor=_actor("approver-1"),
                command_id=uuid.uuid4().hex,
                obligations={"announce_in_incident_channel": "inc-1"},
            )
        )

    # The role stays granted; only the scopes are removed.
    async with maker() as session, session.begin():
        await session.execute(
            update(RoleAssignmentORM)
            .where(RoleAssignmentORM.principal_id == "approver-1")
            .values(scopes=None)
        )

    from hitl_ops.application.revalidation import RevalidationService

    async with maker() as session:
        result = await RevalidationService(session).claim_and_revalidate(
            tenant_id="tenant-1",
            intent_id=pending["intent_id"],
            revision=1,
            worker_id="worker-1",
            command_id=uuid.uuid4().hex,
            target_query=HealthyTarget(),
            bundle=_bundle(),
        )

    assert result.state is IntentState.STALE
    assert result.reason_code == "approver_scopes_revoked"

    async with maker() as session, session.begin():
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
        decisions = (
            (
                await session.execute(
                    select(RoleAssignmentORM).where(RoleAssignmentORM.principal_id == "approver-1")
                )
            )
            .scalars()
            .all()
        )
    assert intent is not None and intent.state == IntentState.STALE.value
    assert decisions and decisions[0].role == "approver"  # role never revoked


async def test_role_grant_with_current_scopes_still_executes(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    await _grant(engine, "approver-1", "approver", scopes=["ops:read", "ops:write"])
    async with maker() as session, session.begin():
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)
    async with maker() as session, session.begin():
        await ApprovalCommandService(session).decide(
            ApprovalDecisionCommand(
                tenant_id="tenant-1",
                intent_id=pending["intent_id"],
                revision=1,
                intent_digest=pending["digest"],
                level=1,
                decision=ApprovalDecision.APPROVE,
                reason="approved",
                expected_state_version=pending["expected_state_version"],
                actor=_actor("approver-1"),
                command_id=uuid.uuid4().hex,
                obligations={"announce_in_incident_channel": "inc-1"},
            )
        )

    from hitl_ops.application.revalidation import ExecutionPermit, RevalidationService

    async with maker() as session:
        result = await RevalidationService(session).claim_and_revalidate(
            tenant_id="tenant-1",
            intent_id=pending["intent_id"],
            revision=1,
            worker_id="worker-1",
            command_id=uuid.uuid4().hex,
            target_query=HealthyTarget(),
            bundle=_bundle(),
        )
    assert isinstance(result, ExecutionPermit)
    assert result.issued_at <= datetime.now(UTC)
