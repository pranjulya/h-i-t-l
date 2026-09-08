"""Approval transaction integration tests: races, distinctness, authority."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.application.commands import ApprovalCommandService, ApprovalDecisionCommand
from hitl_ops.domain.enums import ApprovalDecision, IntentState
from hitl_ops.domain.errors import (
    ApprovalStaleError,
    ForbiddenError,
    IllegalTransitionError,
    NotFoundError,
    StateConflictError,
)
from hitl_ops.infrastructure.authorization import current_roles
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.identity import AuthenticatedActor
from hitl_ops.infrastructure.orm import ApprovalDecisionORM, OutboxMessageORM
from tests.integration.conftest import create_pending_intent, grant_role_directly

_RESTART = {"environment": "staging", "service": "payments-api", "strategy": "rolling"}


def _actor(actor_id: str, tenant_id: str = "tenant-1") -> AuthenticatedActor:
    return AuthenticatedActor(actor_id=actor_id, tenant_id=tenant_id, correlation_id="corr-1")


def _decide_command(
    pending: dict, actor: AuthenticatedActor, **overrides: object
) -> ApprovalDecisionCommand:
    values: dict = {
        "tenant_id": "tenant-1",
        "intent_id": pending["intent_id"],
        "revision": 1,
        "intent_digest": pending["digest"],
        "level": 1,
        "decision": ApprovalDecision.APPROVE,
        "reason": "looks safe",
        "expected_state_version": pending["expected_state_version"],
        "actor": actor,
        "command_id": uuid.uuid4().hex,
    }
    values.update(overrides)
    return ApprovalDecisionCommand(**values)  # type: ignore[arg-type]


async def test_single_approval_commits_decision_transition_and_audit(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "approver-1", "approver")
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)

    async with maker() as session, session.begin():
        snapshot = await ApprovalCommandService(session).decide(
            _decide_command(pending, _actor("approver-1"))
        )

    assert snapshot.state is IntentState.APPROVED
    assert snapshot.state_version == pending["expected_state_version"] + 1

    async with maker() as session, session.begin():
        decision = (
            await session.execute(
                select(ApprovalDecisionORM).where(
                    ApprovalDecisionORM.intent_id == pending["intent_id"]
                )
            )
        ).scalar_one()
        assert decision.decision == "APPROVE"
        assert decision.level == 1
        assert decision.intent_digest == pending["digest"]
        assert "approver" in decision.actor_roles_snapshot
        assert decision.policy_version

        topics = (await session.execute(select(OutboxMessageORM.topic))).scalars().all()
        assert "intent.transitioned" in topics


async def test_requester_cannot_self_approve(migrated_database: str, engine: AsyncEngine) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "user-1", "approver")
        pending = await create_pending_intent(
            session, tool="restart_service", parameters=_RESTART, requester_id="user-1"
        )

    async with maker() as session, session.begin():
        with pytest.raises(ForbiddenError):
            await ApprovalCommandService(session).decide(_decide_command(pending, _actor("user-1")))


async def test_unauthorized_actor_cannot_approve(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)

    async with maker() as session, session.begin():
        with pytest.raises(ForbiddenError):
            await ApprovalCommandService(session).decide(
                _decide_command(pending, _actor("rando-1"))
            )


async def test_stale_digest_is_rejected(migrated_database: str, engine: AsyncEngine) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "approver-1", "approver")
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)

    async with maker() as session, session.begin():
        with pytest.raises(ApprovalStaleError):
            await ApprovalCommandService(session).decide(
                _decide_command(pending, _actor("approver-1"), intent_digest="f" * 64)
            )


async def test_expected_version_mismatch_conflicts(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "approver-1", "approver")
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)

    async with maker() as session, session.begin():
        with pytest.raises(StateConflictError):
            await ApprovalCommandService(session).decide(
                _decide_command(pending, _actor("approver-1"), expected_state_version=99)
            )


async def test_locked_row_conflicts_instead_of_blocking(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "approver-1", "approver")
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)

    async with maker() as first, first.begin():
        await ApprovalCommandService(first).decide(_decide_command(pending, _actor("approver-1")))
        async with maker() as second:
            with pytest.raises(StateConflictError):
                await ApprovalCommandService(second).decide(
                    _decide_command(
                        pending,
                        _actor("approver-2"),
                        expected_state_version=pending["expected_state_version"],
                    )
                )


async def test_second_decision_after_commit_is_illegal(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "approver-1", "approver")
        await grant_role_directly(session, "tenant-1", "approver-2", "approver")
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)

    async with maker() as session, session.begin():
        await ApprovalCommandService(session).decide(_decide_command(pending, _actor("approver-1")))

    async with maker() as session, session.begin():
        with pytest.raises(IllegalTransitionError):
            await ApprovalCommandService(session).decide(
                _decide_command(
                    pending,
                    _actor("approver-2"),
                    expected_state_version=pending["expected_state_version"] + 1,
                )
            )


async def test_critical_two_step_requires_distinct_authorized_approvers(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    delete_params = {
        "environment": "staging",
        "resource_type": "postgres_instance",
        "resource_id": "pg-main-1",
        "deletion_mode": "hard",
    }
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "l1-user", "critical_approver_l1")
        await grant_role_directly(session, "tenant-1", "l2-user", "critical_approver_l2")
        pending = await create_pending_intent(
            session, tool="delete_resource", parameters=delete_params, requester_id="user-1"
        )

    async with maker() as session, session.begin():
        snapshot = await ApprovalCommandService(session).decide(
            _decide_command(pending, _actor("l1-user"), level=1)
        )
    assert snapshot.state is IntentState.PENDING_APPROVAL_2
    assert snapshot.state_version == pending["expected_state_version"] + 2

    # L2 before L1 would be illegal; L1 must not satisfy L2 either.
    async with maker() as session, session.begin():
        with pytest.raises(ForbiddenError):
            await ApprovalCommandService(session).decide(
                _decide_command(
                    pending,
                    _actor("l1-user"),
                    level=2,
                    expected_state_version=snapshot.state_version,
                )
            )

    # Requester cannot approve either level.
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "user-1", "critical_approver_l2")
        with pytest.raises(ForbiddenError):
            await ApprovalCommandService(session).decide(
                _decide_command(
                    pending,
                    _actor("user-1"),
                    level=2,
                    expected_state_version=snapshot.state_version,
                )
            )

    async with maker() as session, session.begin():
        final = await ApprovalCommandService(session).decide(
            _decide_command(
                pending,
                _actor("l2-user"),
                level=2,
                expected_state_version=snapshot.state_version,
            )
        )
    assert final.state is IntentState.APPROVED


async def test_rejection_wins_and_is_terminal(migrated_database: str, engine: AsyncEngine) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "approver-1", "approver")
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)

    async with maker() as session, session.begin():
        snapshot = await ApprovalCommandService(session).decide(
            _decide_command(pending, _actor("approver-1"), decision=ApprovalDecision.REJECT)
        )
    assert snapshot.state is IntentState.REJECTED

    async with maker() as session, session.begin():
        with pytest.raises(IllegalTransitionError):
            await ApprovalCommandService(session).decide(
                _decide_command(
                    pending,
                    _actor("approver-1"),
                    expected_state_version=snapshot.state_version,
                )
            )


async def test_requester_can_cancel_before_claim(
    migrated_database: str, engine: AsyncEngine
) -> None:
    from hitl_ops.application.commands import CancelIntentCommand

    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_pending_intent(
            session, tool="restart_service", parameters=_RESTART, requester_id="user-1"
        )

    async with maker() as session, session.begin():
        snapshot = await ApprovalCommandService(session).cancel(
            CancelIntentCommand(
                tenant_id="tenant-1",
                intent_id=pending["intent_id"],
                revision=1,
                reason="no longer needed",
                expected_state_version=pending["expected_state_version"],
                actor=_actor("user-1"),
                command_id=uuid.uuid4().hex,
            )
        )
    assert snapshot.state is IntentState.CANCELLED

    async with maker() as session, session.begin():
        with pytest.raises(IllegalTransitionError):
            await ApprovalCommandService(session).cancel(
                CancelIntentCommand(
                    tenant_id="tenant-1",
                    intent_id=pending["intent_id"],
                    revision=1,
                    reason="again",
                    expected_state_version=snapshot.state_version,
                    actor=_actor("user-1"),
                    command_id=uuid.uuid4().hex,
                )
            )


async def test_cross_tenant_lookup_does_not_disclose(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "approver-1", "approver")
        pending = await create_pending_intent(
            session, tool="restart_service", parameters=_RESTART, tenant_id="tenant-1"
        )

    async with maker() as session, session.begin():
        command = _decide_command(pending, _actor("approver-1", tenant_id="tenant-2"))
        with pytest.raises(NotFoundError):
            await ApprovalCommandService(session).decide(command)


async def test_server_derives_actor_from_authenticated_context(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "approver-1", "approver")
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)

    async with maker() as session, session.begin():
        await ApprovalCommandService(session).decide(_decide_command(pending, _actor("approver-1")))
        actor_roles = await current_roles(session, _actor("approver-1"), "staging")
        assert "approver" in actor_roles


async def test_partial_unique_index_blocks_duplicate_approvals(
    migrated_database: str, engine: AsyncEngine
) -> None:
    from sqlalchemy.exc import IntegrityError

    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)
        session.add(
            ApprovalDecisionORM(
                tenant_id="tenant-1",
                intent_id=pending["intent_id"],
                intent_revision=1,
                intent_digest=pending["digest"],
                level=1,
                decision="APPROVE",
                actor_id="dup-1",
                actor_roles_snapshot=["approver"],
                scope_snapshot={},
                reason="first",
                policy_version="policy-1",
            )
        )
        session.add(
            ApprovalDecisionORM(
                tenant_id="tenant-1",
                intent_id=pending["intent_id"],
                intent_revision=1,
                intent_digest=pending["digest"],
                level=1,
                decision="APPROVE",
                actor_id="dup-1",
                actor_roles_snapshot=["approver"],
                scope_snapshot={},
                reason="second",
                policy_version="policy-1",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
