"""Approval decision and cancellation routes."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from hitl_ops.api.dependencies import ActorDep, IdempotencyDep, SessionDep
from hitl_ops.application.commands import (
    ApprovalCommandService,
    ApprovalDecisionCommand,
    CancelIntentCommand,
    IdempotencyService,
)
from hitl_ops.domain.enums import ApprovalDecision
from hitl_ops.domain.models import IntentSnapshot

router = APIRouter(prefix="/v1")


class ApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    intent_digest: str = Field(min_length=64, max_length=64)
    level: int = Field(ge=1, le=2)
    decision: Literal["APPROVE", "REJECT"]
    reason: str = Field(min_length=1, max_length=2000)
    expected_state_version: int = Field(ge=1)
    obligations: dict[str, str] = Field(default_factory=dict)


class CancelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    expected_state_version: int = Field(ge=1)


def _snapshot(snapshot: IntentSnapshot) -> dict[str, Any]:
    return {
        "intent_id": str(snapshot.intent_id),
        "revision": snapshot.revision,
        "digest": snapshot.intent_digest,
        "state": snapshot.state.value,
        "state_version": snapshot.state_version,
    }


@router.post("/intents/{intent_id}/approvals")
async def decide_approval(
    session: SessionDep,
    actor: ActorDep,
    intent_id: uuid.UUID,
    body: ApprovalBody,
    idempotency_key: IdempotencyDep,
) -> dict[str, Any]:
    idempotency = IdempotencyService(session)
    reservation = await idempotency.begin(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        scope="approvals",
        key=idempotency_key,
        request_payload={"intent_id": str(intent_id), **body.model_dump()},
    )
    if reservation.replayed and reservation.response_body is not None:
        return reservation.response_body
    service = ApprovalCommandService(session)
    snapshot = await service.decide(
        ApprovalDecisionCommand(
            tenant_id=actor.tenant_id,
            intent_id=intent_id,
            revision=body.revision,
            intent_digest=body.intent_digest,
            level=body.level,
            decision=ApprovalDecision(body.decision),
            reason=body.reason,
            expected_state_version=body.expected_state_version,
            actor=actor,
            command_id=uuid.uuid4().hex,
            obligations=body.obligations,
        )
    )
    response = _snapshot(snapshot)
    await idempotency.complete(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        scope="approvals",
        key=idempotency_key,
        response_status=200,
        response_body=response,
    )
    return response


@router.post("/intents/{intent_id}/cancel")
async def cancel_intent(
    session: SessionDep,
    actor: ActorDep,
    intent_id: uuid.UUID,
    body: CancelBody,
    idempotency_key: IdempotencyDep,
) -> dict[str, Any]:
    idempotency = IdempotencyService(session)
    reservation = await idempotency.begin(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        scope="cancellations",
        key=idempotency_key,
        request_payload={"intent_id": str(intent_id), **body.model_dump()},
    )
    if reservation.replayed and reservation.response_body is not None:
        return reservation.response_body
    service = ApprovalCommandService(session)
    snapshot = await service.cancel(
        CancelIntentCommand(
            tenant_id=actor.tenant_id,
            intent_id=intent_id,
            revision=body.revision,
            reason=body.reason,
            expected_state_version=body.expected_state_version,
            actor=actor,
            command_id=uuid.uuid4().hex,
        )
    )
    response = _snapshot(snapshot)
    await idempotency.complete(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        scope="cancellations",
        key=idempotency_key,
        response_status=200,
        response_body=response,
    )
    return response
