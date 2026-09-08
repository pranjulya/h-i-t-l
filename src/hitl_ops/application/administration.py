"""Administrator commands: role assignments and policy bundle lifecycle.

Every administration change requires the Administrator role, appends an
administrative audit outbox event (LLD §10), and never alters historic
approval evidence. Policy bundles are authored in source control; these
commands register/activate reviewed versions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hitl_ops.domain.errors import ForbiddenError, NotFoundError
from hitl_ops.infrastructure.authorization import require_administrator
from hitl_ops.infrastructure.identity import AuthenticatedActor
from hitl_ops.infrastructure.orm import OutboxMessageORM, PolicyBundleORM, RoleAssignmentORM


@dataclass(frozen=True, slots=True)
class GrantRoleCommand:
    tenant_id: str
    principal_id: str
    role: str
    environments: tuple[str, ...]
    valid_until: datetime | None
    actor: AuthenticatedActor
    command_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class RevokeRoleCommand:
    tenant_id: str
    assignment_id: uuid.UUID
    actor: AuthenticatedActor
    command_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class RegisterPolicyBundleCommand:
    version: str
    rules: dict[str, Any]
    actor: AuthenticatedActor
    command_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ActivatePolicyBundleCommand:
    version: str
    actor: AuthenticatedActor
    command_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class RoleAssignmentSnapshot:
    assignment_id: uuid.UUID
    tenant_id: str
    principal_id: str
    role: str
    environments: tuple[str, ...]
    valid_until: datetime | None
    revoked_at: datetime | None


def _audit(session: AsyncSession, topic: str, payload: dict[str, Any]) -> None:
    session.add(OutboxMessageORM(topic=topic, payload=payload))


class AdministrationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def grant_role(self, command: GrantRoleCommand) -> RoleAssignmentSnapshot:
        await require_administrator(self._session, command.actor)
        row = RoleAssignmentORM(
            tenant_id=command.tenant_id,
            principal_id=command.principal_id,
            role=command.role,
            environments=list(command.environments) or None,
            valid_until=command.valid_until,
            granted_by=command.actor.actor_id,
        )
        self._session.add(row)
        await self._session.flush()
        _audit(
            self._session,
            "administration.role_changed",
            {
                "tenant_id": command.tenant_id,
                "principal_id": command.principal_id,
                "role": command.role,
                "action": "granted",
                "actor_id": command.actor.actor_id,
                "command_id": command.command_id,
                "reason": command.reason,
            },
        )
        return RoleAssignmentSnapshot(
            assignment_id=row.id,
            tenant_id=row.tenant_id,
            principal_id=row.principal_id,
            role=row.role,
            environments=tuple(row.environments or ()),
            valid_until=row.valid_until,
            revoked_at=row.revoked_at,
        )

    async def revoke_role(self, command: RevokeRoleCommand) -> RoleAssignmentSnapshot:
        await require_administrator(self._session, command.actor)
        row = await self._session.get(RoleAssignmentORM, command.assignment_id)
        if row is None or row.tenant_id != command.tenant_id:
            raise NotFoundError("role assignment not found")
        if row.revoked_at is not None:
            raise ForbiddenError("assignment is already revoked")
        row.revoked_at = datetime.now(UTC)
        _audit(
            self._session,
            "administration.role_changed",
            {
                "tenant_id": command.tenant_id,
                "principal_id": row.principal_id,
                "role": row.role,
                "action": "revoked",
                "actor_id": command.actor.actor_id,
                "command_id": command.command_id,
                "reason": command.reason,
            },
        )
        await self._session.flush()
        return RoleAssignmentSnapshot(
            assignment_id=row.id,
            tenant_id=row.tenant_id,
            principal_id=row.principal_id,
            role=row.role,
            environments=tuple(row.environments or ()),
            valid_until=row.valid_until,
            revoked_at=row.revoked_at,
        )

    async def register_policy_bundle(self, command: RegisterPolicyBundleCommand) -> str:
        await require_administrator(self._session, command.actor)
        existing = (
            await self._session.execute(
                select(PolicyBundleORM).where(PolicyBundleORM.version == command.version)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ForbiddenError("policy bundle version already registered")
        row = PolicyBundleORM(version=command.version, rules=command.rules, is_active=False)
        self._session.add(row)
        _audit(
            self._session,
            "administration.policy_changed",
            {
                "policy_version": command.version,
                "action": "registered",
                "actor_id": command.actor.actor_id,
                "command_id": command.command_id,
                "reason": command.reason,
            },
        )
        await self._session.flush()
        return command.version

    async def activate_policy_bundle(self, command: ActivatePolicyBundleCommand) -> str:
        await require_administrator(self._session, command.actor)
        target = (
            await self._session.execute(
                select(PolicyBundleORM).where(PolicyBundleORM.version == command.version)
            )
        ).scalar_one_or_none()
        if target is None:
            raise NotFoundError("policy bundle version not found")
        await self._session.execute(
            update(PolicyBundleORM)
            .where(PolicyBundleORM.is_active.is_(True))
            .values(is_active=False)
        )
        target.is_active = True
        _audit(
            self._session,
            "administration.policy_changed",
            {
                "policy_version": command.version,
                "action": "activated",
                "actor_id": command.actor.actor_id,
                "command_id": command.command_id,
                "reason": command.reason,
            },
        )
        await self._session.flush()
        return command.version
