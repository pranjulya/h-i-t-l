"""Current-authorization lookup shared by command handlers."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hitl_ops.domain.errors import ForbiddenError
from hitl_ops.infrastructure.identity import (
    AuthenticatedActor,
    RoleAssignment,
    evaluate_current_roles,
)
from hitl_ops.infrastructure.orm import RoleAssignmentORM


async def load_assignments(
    session: AsyncSession, tenant_id: str, principal_id: str
) -> tuple[RoleAssignment, ...]:
    rows = (
        (
            await session.execute(
                select(RoleAssignmentORM).where(
                    RoleAssignmentORM.tenant_id == tenant_id,
                    RoleAssignmentORM.principal_id == principal_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return tuple(
        RoleAssignment(
            role=row.role,
            valid_from=row.valid_from,
            valid_until=row.valid_until,
            revoked_at=row.revoked_at,
            environments=tuple(row.environments or ()),
        )
        for row in rows
    )


async def current_roles(
    session: AsyncSession, actor: AuthenticatedActor, environment: str | None
) -> frozenset[str]:
    assignments = await load_assignments(session, actor.tenant_id, actor.actor_id)
    return evaluate_current_roles(assignments, datetime.now(UTC), environment)


async def require_administrator(session: AsyncSession, actor: AuthenticatedActor) -> None:
    roles = await current_roles(session, actor, None)
    if "administrator" not in roles:
        raise ForbiddenError("administrator role is required")
