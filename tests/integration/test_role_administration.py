"""Role/policy administration integration tests: gate, audit, lifecycle."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.application.administration import (
    ActivatePolicyBundleCommand,
    AdministrationService,
    GrantRoleCommand,
    RegisterPolicyBundleCommand,
    RevokeRoleCommand,
)
from hitl_ops.domain.errors import ForbiddenError, NotFoundError, StateConflictError
from hitl_ops.infrastructure.authorization import current_roles
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.identity import AuthenticatedActor
from hitl_ops.infrastructure.orm import OutboxMessageORM
from tests.integration.conftest import grant_role_directly


def _actor(actor_id: str) -> AuthenticatedActor:
    return AuthenticatedActor(actor_id=actor_id, tenant_id="tenant-1", correlation_id="corr-1")


def _grant(principal: str, role: str, actor: AuthenticatedActor) -> GrantRoleCommand:
    return GrantRoleCommand(
        tenant_id="tenant-1",
        principal_id=principal,
        role=role,
        environments=("staging",),
        valid_until=None,
        actor=actor,
        command_id=uuid.uuid4().hex,
        reason="grant for testing",
    )


async def test_administrator_grants_and_revokes_roles_with_audit(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "admin-1", "administrator")

    async with maker() as session, session.begin():
        snapshot = await AdministrationService(session).grant_role(
            _grant("approver-1", "approver", _actor("admin-1"))
        )
    assert snapshot.role == "approver"
    assert snapshot.environments == ("staging",)

    async with maker() as session, session.begin():
        roles = await current_roles(session, _actor("approver-1"), "staging")
        assert "approver" in roles
        # Scoped assignment does not apply to production.
        prod_roles = await current_roles(session, _actor("approver-1"), "production")
        assert "approver" not in prod_roles

    async with maker() as session, session.begin():
        revoked = await AdministrationService(session).revoke_role(
            RevokeRoleCommand(
                tenant_id="tenant-1",
                assignment_id=snapshot.assignment_id,
                actor=_actor("admin-1"),
                command_id=uuid.uuid4().hex,
                reason="rotating access",
            )
        )
    assert revoked.revoked_at is not None

    async with maker() as session, session.begin():
        roles_after = await current_roles(session, _actor("approver-1"), "staging")
        assert "approver" not in roles_after

    async with maker() as session, session.begin():
        topics = (
            (
                await session.execute(
                    select(OutboxMessageORM.topic).where(
                        OutboxMessageORM.topic == "administration.role_changed"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(topics) == 2


async def test_non_administrator_cannot_administer(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending_assignment = await grant_role_directly(
            session, "tenant-1", "approver-1", "approver"
        )

    async with maker() as session, session.begin():
        with pytest.raises(ForbiddenError):
            await AdministrationService(session).grant_role(
                _grant("someone-else", "approver", _actor("approver-1"))
            )
        with pytest.raises(ForbiddenError):
            await AdministrationService(session).revoke_role(
                RevokeRoleCommand(
                    tenant_id="tenant-1",
                    assignment_id=pending_assignment,
                    actor=_actor("approver-1"),
                    command_id=uuid.uuid4().hex,
                    reason="escalation attempt",
                )
            )


async def test_policy_bundle_lifecycle_is_administrator_gated(
    migrated_database: str, engine: AsyncEngine
) -> None:
    from hitl_ops.domain.policy import SEED_POLICY_BUNDLE_RULES, PolicyBundle
    from hitl_ops.infrastructure.repositories import PolicyBundleRepository

    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "admin-1", "administrator")

    async with maker() as session, session.begin():
        with pytest.raises(ForbiddenError):
            await AdministrationService(session).register_policy_bundle(
                RegisterPolicyBundleCommand(
                    version="policy-2",
                    rules=SEED_POLICY_BUNDLE_RULES,
                    actor=_actor("approver-1"),
                    command_id=uuid.uuid4().hex,
                    reason="unauthorized",
                )
            )

    new_rules = {
        **SEED_POLICY_BUNDLE_RULES,
        "tools": {
            **SEED_POLICY_BUNDLE_RULES["tools"],
            "inspect_service": {"disposition": "BLOCK"},
        },
    }
    async with maker() as session, session.begin():
        await AdministrationService(session).register_policy_bundle(
            RegisterPolicyBundleCommand(
                version="policy-2",
                rules=new_rules,
                actor=_actor("admin-1"),
                command_id=uuid.uuid4().hex,
                reason="stricter read policy",
            )
        )

    async with maker() as session, session.begin():
        with pytest.raises(StateConflictError):
            await AdministrationService(session).register_policy_bundle(
                RegisterPolicyBundleCommand(
                    version="policy-2",
                    rules=new_rules,
                    actor=_actor("admin-1"),
                    command_id=uuid.uuid4().hex,
                    reason="duplicate",
                )
            )

    async with maker() as session, session.begin():
        active = await PolicyBundleRepository(session).get_active("tenant-1")
        assert active is not None and active.version == "policy-1"

    async with maker() as session, session.begin():
        await AdministrationService(session).activate_policy_bundle(
            ActivatePolicyBundleCommand(
                version="policy-2",
                actor=_actor("admin-1"),
                command_id=uuid.uuid4().hex,
                reason="enable new policy",
            )
        )

    async with maker() as session, session.begin():
        active = await PolicyBundleRepository(session).get_active("tenant-1")
        assert active is not None
        assert active.version == "policy-2"
        assert isinstance(active, PolicyBundle)

    async with maker() as session, session.begin():
        with pytest.raises(NotFoundError):
            await AdministrationService(session).activate_policy_bundle(
                ActivatePolicyBundleCommand(
                    version="policy-missing",
                    actor=_actor("admin-1"),
                    command_id=uuid.uuid4().hex,
                    reason="typo",
                )
            )

    async with maker() as session, session.begin():
        topics = (
            (
                await session.execute(
                    select(OutboxMessageORM.topic).where(
                        OutboxMessageORM.topic == "administration.policy_changed"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(topics) == 2  # registered + activated; the forbidden attempt is not audited


async def test_duplicate_grant_returns_single_active_assignment(
    migrated_database: str, engine: AsyncEngine
) -> None:
    from sqlalchemy import func, select

    from hitl_ops.infrastructure.orm import RoleAssignmentORM

    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await grant_role_directly(session, "tenant-1", "admin-1", "administrator")

    async with maker() as session, session.begin():
        first = await AdministrationService(session).grant_role(
            _grant("approver-1", "approver", _actor("admin-1"))
        )
    async with maker() as session, session.begin():
        second = await AdministrationService(session).grant_role(
            _grant("approver-1", "approver", _actor("admin-1"))
        )
    assert second.assignment_id == first.assignment_id

    async with maker() as session, session.begin():
        count = (
            await session.execute(
                select(func.count())
                .select_from(RoleAssignmentORM)
                .where(
                    RoleAssignmentORM.tenant_id == "tenant-1",
                    RoleAssignmentORM.principal_id == "approver-1",
                    RoleAssignmentORM.role == "approver",
                    RoleAssignmentORM.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        assert count == 1

    # Revoking the visible assignment removes the authority: no hidden second
    # grant survives behind it.
    async with maker() as session, session.begin():
        await AdministrationService(session).revoke_role(
            RevokeRoleCommand(
                tenant_id="tenant-1",
                assignment_id=first.assignment_id,
                actor=_actor("admin-1"),
                command_id=uuid.uuid4().hex,
                reason="revoke the single grant",
            )
        )
    async with maker() as session, session.begin():
        roles = await current_roles(session, _actor("approver-1"), "staging")
        assert "approver" not in roles
