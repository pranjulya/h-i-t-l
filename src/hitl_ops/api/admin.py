"""Administrator routes: role assignments and policy bundle lifecycle."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hitl_ops.api.dependencies import ActorDep, SessionDep
from hitl_ops.application.administration import (
    ActivatePolicyBundleCommand,
    AdministrationService,
    GrantRoleCommand,
    RegisterPolicyBundleCommand,
    RevokeRoleCommand,
)

router = APIRouter(prefix="/v1/admin")


class GrantRoleBody(BaseModel):
    principal_id: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=63)
    environments: list[str] = Field(default_factory=list, max_length=10)
    valid_until: datetime | None = None
    reason: str = Field(min_length=1, max_length=2000)


class RevokeRoleBody(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class RegisterBundleBody(BaseModel):
    version: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9-]+$")
    rules: dict[str, Any]
    reason: str = Field(min_length=1, max_length=2000)


class ActivateBundleBody(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/role-assignments", status_code=201)
async def grant_role(session: SessionDep, actor: ActorDep, body: GrantRoleBody) -> dict[str, Any]:
    service = AdministrationService(session)
    snapshot = await service.grant_role(
        GrantRoleCommand(
            tenant_id=actor.tenant_id,
            principal_id=body.principal_id,
            role=body.role,
            environments=tuple(body.environments),
            valid_until=body.valid_until,
            actor=actor,
            command_id=uuid.uuid4().hex,
            reason=body.reason,
        )
    )
    return {
        "assignment_id": str(snapshot.assignment_id),
        "principal_id": snapshot.principal_id,
        "role": snapshot.role,
        "environments": list(snapshot.environments),
        "valid_until": snapshot.valid_until.isoformat() if snapshot.valid_until else None,
    }


@router.post("/role-assignments/{assignment_id}/revoke")
async def revoke_role(
    session: SessionDep, actor: ActorDep, assignment_id: uuid.UUID, body: RevokeRoleBody
) -> dict[str, Any]:
    service = AdministrationService(session)
    snapshot = await service.revoke_role(
        RevokeRoleCommand(
            tenant_id=actor.tenant_id,
            assignment_id=assignment_id,
            actor=actor,
            command_id=uuid.uuid4().hex,
            reason=body.reason,
        )
    )
    return {
        "assignment_id": str(snapshot.assignment_id),
        "revoked_at": snapshot.revoked_at.isoformat() if snapshot.revoked_at else None,
    }


@router.post("/policy-bundles", status_code=201)
async def register_policy_bundle(
    session: SessionDep, actor: ActorDep, body: RegisterBundleBody
) -> dict[str, Any]:
    service = AdministrationService(session)
    version = await service.register_policy_bundle(
        RegisterPolicyBundleCommand(
            version=body.version,
            rules=body.rules,
            actor=actor,
            command_id=uuid.uuid4().hex,
            reason=body.reason,
        )
    )
    return {"version": version, "active": False}


@router.post("/policy-bundles/{version}/activate")
async def activate_policy_bundle(
    session: SessionDep, actor: ActorDep, version: str, body: ActivateBundleBody
) -> dict[str, Any]:
    service = AdministrationService(session)
    activated = await service.activate_policy_bundle(
        ActivatePolicyBundleCommand(
            version=version,
            actor=actor,
            command_id=uuid.uuid4().hex,
            reason=body.reason,
        )
    )
    return {"version": activated, "active": True}
