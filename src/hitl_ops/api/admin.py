"""Administrator routes: role assignments and policy bundle lifecycle."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from hitl_ops.api.dependencies import ActorDep, IdempotencyDep, SessionDep
from hitl_ops.application.administration import (
    ActivatePolicyBundleCommand,
    AdministrationService,
    GrantRoleCommand,
    RegisterPolicyBundleCommand,
    RevokeRoleCommand,
)
from hitl_ops.application.commands import IdempotencyService

router = APIRouter(prefix="/v1/admin")


class GrantRoleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_id: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=63)
    environments: list[str] = Field(default_factory=list, max_length=10)
    valid_until: datetime | None = None
    reason: str = Field(min_length=1, max_length=2000)


class RevokeRoleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


class RegisterBundleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9-]+$")
    rules: dict[str, Any]
    reason: str = Field(min_length=1, max_length=2000)


class ActivateBundleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


async def _reserve(
    session: SessionDep,
    actor: ActorDep,
    scope: str,
    key: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    from hitl_ops.domain.errors import DomainError

    reservation = await IdempotencyService(session).begin(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        scope=scope,
        key=key,
        request_payload=payload,
    )
    if reservation.replayed and reservation.response_body is not None:
        return reservation.response_body
    if reservation.replayed and reservation.pending:
        raise DomainError(
            "an identical command is still in progress",
            code="STATE_CONFLICT",
            http_status=409,
            retryable=True,
        )
    return None


@router.post("/role-assignments", status_code=201)
async def grant_role(
    session: SessionDep, actor: ActorDep, body: GrantRoleBody, idempotency_key: IdempotencyDep
) -> dict[str, Any]:
    replayed = await _reserve(
        session, actor, "admin_role_assignments", idempotency_key, body.model_dump(mode="json")
    )
    if replayed is not None:
        return replayed
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
    response = {
        "assignment_id": str(snapshot.assignment_id),
        "principal_id": snapshot.principal_id,
        "role": snapshot.role,
        "environments": list(snapshot.environments),
        "valid_until": snapshot.valid_until.isoformat() if snapshot.valid_until else None,
    }
    await IdempotencyService(session).complete(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        scope="admin_role_assignments",
        key=idempotency_key,
        response_status=201,
        response_body=response,
    )
    return response


@router.post("/role-assignments/{assignment_id}/revoke")
async def revoke_role(
    session: SessionDep,
    actor: ActorDep,
    assignment_id: uuid.UUID,
    body: RevokeRoleBody,
    idempotency_key: IdempotencyDep,
) -> dict[str, Any]:
    replayed = await _reserve(
        session,
        actor,
        "admin_role_revocations",
        idempotency_key,
        {"assignment_id": str(assignment_id), **body.model_dump(mode="json")},
    )
    if replayed is not None:
        return replayed
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
    response = {
        "assignment_id": str(snapshot.assignment_id),
        "revoked_at": snapshot.revoked_at.isoformat() if snapshot.revoked_at else None,
    }
    await IdempotencyService(session).complete(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        scope="admin_role_revocations",
        key=idempotency_key,
        response_status=200,
        response_body=response,
    )
    return response


@router.post("/policy-bundles", status_code=201)
async def register_policy_bundle(
    session: SessionDep, actor: ActorDep, body: RegisterBundleBody, idempotency_key: IdempotencyDep
) -> dict[str, Any]:
    replayed = await _reserve(
        session, actor, "admin_policy_bundles", idempotency_key, body.model_dump(mode="json")
    )
    if replayed is not None:
        return replayed
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
    response = {"version": version, "active": False}
    await IdempotencyService(session).complete(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        scope="admin_policy_bundles",
        key=idempotency_key,
        response_status=201,
        response_body=response,
    )
    return response


@router.post("/policy-bundles/{version}/activate")
async def activate_policy_bundle(
    session: SessionDep,
    actor: ActorDep,
    version: str,
    body: ActivateBundleBody,
    idempotency_key: IdempotencyDep,
) -> dict[str, Any]:
    replayed = await _reserve(
        session,
        actor,
        "admin_policy_activations",
        idempotency_key,
        {"version": version, **body.model_dump(mode="json")},
    )
    if replayed is not None:
        return replayed
    service = AdministrationService(session)
    activated = await service.activate_policy_bundle(
        ActivatePolicyBundleCommand(
            version=version,
            actor=actor,
            command_id=uuid.uuid4().hex,
            reason=body.reason,
        )
    )
    response = {"version": activated, "active": True}
    await IdempotencyService(session).complete(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        scope="admin_policy_activations",
        key=idempotency_key,
        response_status=200,
        response_body=response,
    )
    return response
