"""Integration fixtures: schema reset and migration rehearsal helpers."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession

from hitl_ops.config import Settings

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def run_alembic_upgrade(database_url: str, target: str) -> None:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, target)


def run_alembic_downgrade(database_url: str, target: str) -> None:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(cfg, target)


def reset_schema(database_url: str) -> None:
    async def reset() -> None:
        from sqlalchemy import text

        from hitl_ops.infrastructure.database import build_engine

        engine = build_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("DROP SCHEMA public CASCADE"))
                await connection.execute(text("CREATE SCHEMA public"))
        finally:
            await engine.dispose()

    asyncio.run(reset())


@pytest.fixture
def migrated_database(settings: Settings) -> str:
    database_url = settings.database_url
    reset_schema(database_url)
    run_alembic_upgrade(database_url, "head")
    return database_url


async def grant_role_directly(
    session: AsyncSession,
    tenant_id: str,
    principal_id: str,
    role: str,
    environments: list[str] | None = None,
) -> uuid.UUID:
    """Seed a role assignment without the administration gate (bootstrap)."""

    from hitl_ops.infrastructure.orm import RoleAssignmentORM

    row = RoleAssignmentORM(
        tenant_id=tenant_id,
        principal_id=principal_id,
        role=role,
        environments=environments,
        granted_by="bootstrap",
    )
    session.add(row)
    await session.flush()
    return row.id


async def create_pending_intent(
    session: AsyncSession,
    *,
    tool: str,
    parameters: dict,
    tenant_id: str = "tenant-1",
    requester_id: str = "user-1",
) -> dict:
    """Create an intent and run evaluation, leaving it pending approval."""

    from hitl_ops.agent.schemas import parse_tool_parameters
    from hitl_ops.domain.enums import IntentSource, ToolName
    from hitl_ops.domain.intent import canonicalize
    from hitl_ops.domain.models import CreateIntentCommand
    from hitl_ops.domain.policy import (
        SEED_POLICY_BUNDLE_RULES,
        SEED_POLICY_BUNDLE_VERSION,
        PolicyBundle,
        evaluate_policy,
    )
    from hitl_ops.domain.risk import RiskContext, evaluate_risk
    from hitl_ops.infrastructure.repositories import EvaluationRepository, IntentRepository

    parsed = parse_tool_parameters(tool, parameters)
    intent = canonicalize(ToolName(tool), tenant_id, requester_id, 1, parsed)
    command = CreateIntentCommand(
        tenant_id=tenant_id,
        intent_id=uuid.uuid4(),
        revision=1,
        tool=ToolName(tool),
        canonical_parameters=intent.parameters,
        intent_digest=intent.digest,
        requester_id=requester_id,
        requester_rationale="integration test",
        source=IntentSource.DIRECT,
        command_id=uuid.uuid4().hex,
        correlation_id=uuid.uuid4().hex,
        actor_id=requester_id,
    )
    await IntentRepository(session).create(command)
    bundle = PolicyBundle(version=SEED_POLICY_BUNDLE_VERSION, rules=SEED_POLICY_BUNDLE_RULES)
    risk = evaluate_risk(command.tool, command.canonical_parameters, RiskContext())
    policy = evaluate_policy(command.tool, risk, command.canonical_parameters, bundle)
    await EvaluationRepository(session).record(
        tenant_id,
        command.intent_id,
        1,
        risk,
        policy,
        command_id=command.command_id,
        correlation_id=command.correlation_id,
    )
    return {
        "intent_id": command.intent_id,
        "digest": intent.digest,
        "expected_state_version": 4,
        "requester_id": requester_id,
        "tenant_id": tenant_id,
    }


APPROVAL_ROLES_BY_TOOL: dict[str, tuple[str, ...]] = {
    "restart_service": ("approver",),
    "scale_service": ("approver",),
    "provision_resource": ("approver",),
    "delete_resource": ("critical_approver_l1", "critical_approver_l2"),
}


async def create_approved_intent(
    session: AsyncSession,
    *,
    tool: str,
    parameters: dict,
    tenant_id: str = "tenant-1",
    requester_id: str = "user-1",
    approvers: tuple[str, ...] = ("approver-1",),
) -> dict:
    """Create, evaluate, and fully approve an intent; ready for a claim."""

    from hitl_ops.application.commands import ApprovalCommandService, ApprovalDecisionCommand
    from hitl_ops.domain.enums import ApprovalDecision
    from hitl_ops.infrastructure.identity import AuthenticatedActor
    from hitl_ops.infrastructure.orm import RoleAssignmentORM

    pending = await create_pending_intent(
        session, tool=tool, parameters=parameters, tenant_id=tenant_id, requester_id=requester_id
    )
    roles = APPROVAL_ROLES_BY_TOOL[tool]
    for approver, role in zip(approvers, roles, strict=False):
        session.add(
            RoleAssignmentORM(
                tenant_id=tenant_id,
                principal_id=approver,
                role=role,
                environments=None,
                granted_by="bootstrap",
            )
        )
        pending[f"approver_{len(pending)}"] = approver
    await session.flush()

    levels = [1] if len(roles) == 1 else [1, 2]
    version = pending["expected_state_version"]
    for level, approver in zip(levels, approvers, strict=False):
        actor = AuthenticatedActor(
            actor_id=approver, tenant_id=tenant_id, correlation_id="corr-test"
        )
        snapshot = await ApprovalCommandService(session).decide(
            ApprovalDecisionCommand(
                tenant_id=tenant_id,
                intent_id=pending["intent_id"],
                revision=1,
                intent_digest=pending["digest"],
                level=level,
                decision=ApprovalDecision.APPROVE,
                reason="test approval",
                expected_state_version=version,
                actor=actor,
                command_id=uuid.uuid4().hex,
            )
        )
        version = snapshot.state_version
    pending["expected_state_version"] = version
    return pending
