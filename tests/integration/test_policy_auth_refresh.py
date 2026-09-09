"""Authorization and policy refresh during revalidation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.adapters.base import TargetSnapshot
from hitl_ops.application.revalidation import RevalidationService
from hitl_ops.domain.enums import IntentState
from hitl_ops.domain.policy import SEED_POLICY_BUNDLE_RULES, PolicyBundle
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.orm import ActionIntentORM, RoleAssignmentORM
from tests.integration.conftest import create_approved_intent

_RESTART = {"environment": "staging", "service": "payments-api", "strategy": "rolling"}


class HealthyTarget:
    async def fetch(self, tool: object, parameters: dict) -> TargetSnapshot:
        return TargetSnapshot(
            found=True, identity={"service": parameters.get("service")}, health="healthy"
        )


def _bundle(**tool_rules: dict) -> PolicyBundle:
    tools = {**SEED_POLICY_BUNDLE_RULES["tools"], **tool_rules}
    return PolicyBundle(
        version="policy-refresh", rules={**SEED_POLICY_BUNDLE_RULES, "tools": tools}
    )


async def _claim(session, pending: dict, bundle: PolicyBundle):
    return await RevalidationService(session).claim_and_revalidate(
        tenant_id="tenant-1",
        intent_id=pending["intent_id"],
        revision=1,
        worker_id="worker-1",
        command_id=uuid.uuid4().hex,
        target_query=HealthyTarget(),
        bundle=bundle,
    )


async def test_revoked_approver_is_observed_at_claim(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="restart_service", parameters=_RESTART)
        await session.execute(
            update(RoleAssignmentORM)
            .where(
                RoleAssignmentORM.tenant_id == "tenant-1",
                RoleAssignmentORM.principal_id == "approver-1",
            )
            .values(revoked_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    async with maker() as session, session.begin():
        failure = await _claim(session, pending, _bundle())
    assert failure is not None
    assert failure.state is IntentState.STALE
    assert failure.reason_code == "approver_no_longer_authorized"


async def test_stricter_policy_is_observed_at_claim(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="restart_service", parameters=_RESTART)

    stricter = _bundle(restart_service={"disposition": "BLOCK"})
    async with maker() as session, session.begin():
        failure = await _claim(session, pending, stricter)
    assert failure is not None
    assert failure.state is IntentState.STALE
    assert failure.reason_code == "policy_now_blocks"


async def test_expired_approval_moves_to_expired_at_claim(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="restart_service", parameters=_RESTART)
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
        assert intent is not None
        intent.approval_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    async with maker() as session, session.begin():
        failure = await _claim(session, pending, _bundle())
    assert failure is not None
    assert failure.state is IntentState.EXPIRED
    assert failure.reason_code == "approval_ttl_elapsed"


async def test_equal_policy_and_current_authorization_proceed(
    migrated_database: str, engine: AsyncEngine
) -> None:
    from hitl_ops.application.revalidation import ExecutionPermit

    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="restart_service", parameters=_RESTART)

    async with maker() as session, session.begin():
        result = await _claim(session, pending, _bundle())
    assert isinstance(result, ExecutionPermit)
    assert result.tool == "restart_service"


async def test_role_scope_change_is_observed_at_claim(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        # Approver role scoped to a different environment than the intent target.
        pending = await create_approved_intent(session, tool="restart_service", parameters=_RESTART)
        await session.execute(
            update(RoleAssignmentORM)
            .where(
                RoleAssignmentORM.tenant_id == "tenant-1",
                RoleAssignmentORM.principal_id == "approver-1",
            )
            .values(environments=["production"])
        )

    async with maker() as session, session.begin():
        failure = await _claim(session, pending, _bundle())
    assert failure is not None
    assert failure.reason_code == "approver_no_longer_authorized"


async def test_expiry_error_is_not_raised_by_claim(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="restart_service", parameters=_RESTART)
        intent = await session.get(ActionIntentORM, ("tenant-1", pending["intent_id"], 1))
        assert intent is not None
        intent.approval_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    async with maker() as session, session.begin():
        failure = await _claim(session, pending, _bundle())
    assert failure is not None
    assert failure.state is IntentState.EXPIRED
    assert failure.reason_code == "approval_ttl_elapsed"
