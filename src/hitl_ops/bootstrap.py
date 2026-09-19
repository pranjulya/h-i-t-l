"""Bootstrap demo identities and the seed policy bundle for a fresh database.

Idempotent: role assignments are inserted only when missing, and the seed
policy bundle activation is left to the existing migration. This is a
learning-mode bootstrap, not a production provisioning path.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from hitl_ops.config import Settings
from hitl_ops.infrastructure.database import build_engine, build_sessionmaker
from hitl_ops.infrastructure.orm import RoleAssignmentORM

# Role and the scopes it grants. Scopes are current DB authority (ADR-008), so
# the demo identities must carry the scopes the seed policy requires.
_DEMO_ROLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("user-1", "approver", ("ops:read", "ops:write")),
    ("approver-1", "approver", ("ops:read", "ops:write")),
    ("l1-user", "critical_approver_l1", ("ops:read", "ops:write")),
    ("l2-user", "critical_approver_l2", ("ops:read", "ops:write")),
    ("admin-1", "administrator", ("ops:read", "ops:write")),
)


async def _bootstrap() -> None:
    settings = Settings()
    engine = build_engine(settings.database_url)
    maker = build_sessionmaker(engine)
    try:
        async with maker() as session, session.begin():
            for principal, role, scopes in _DEMO_ROLES:
                existing = (
                    await session.execute(
                        select(RoleAssignmentORM).where(
                            RoleAssignmentORM.tenant_id == "tenant-1",
                            RoleAssignmentORM.principal_id == principal,
                            RoleAssignmentORM.role == role,
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    session.add(
                        RoleAssignmentORM(
                            tenant_id="tenant-1",
                            principal_id=principal,
                            role=role,
                            environments=None,
                            scopes=list(scopes),
                            granted_by="bootstrap",
                        )
                    )
                elif tuple(existing.scopes or ()) != scopes:
                    # Backfill scopes on grants created before scopes were
                    # DB-backed; roles without scopes cannot approve.
                    existing.scopes = list(scopes)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_bootstrap())


if __name__ == "__main__":
    main()
