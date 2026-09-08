"""Approval decision and cancellation routes."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hitl_ops.api.dependencies import ActorDep, SessionDep
from hitl_ops.application.commands import (
    ApprovalCommandService,
    ApprovalDecisionCommand,
    CancelIntentCommand,
)
from hitl_ops.domain.enums import ApprovalDecision
from hitl_ops.domain.models import IntentSnapshot

router = APIRouter(prefix="/v1")


class ApprovalBody(BaseModel):
    revision: int = Field(ge=1)
    intent_digest: str = Field(min_length=64, max_length=64)
    level: int = Field(ge=1, le=2)
    decision: Literal["APPROVE", "REJECT"]
    reason: str = Field(min_length=1, max_length=2000)
    expected_state_version: int = Field(ge=1)


class CancelBody(BaseModel):
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
    session: SessionDep, actor: ActorDep, intent_id: uuid.UUID, body: ApprovalBody
) -> dict[str, Any]:
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
        )
    )
    return _snapshot(snapshot)


@router.post("/intents/{intent_id}/cancel")
async def cancel_intent(
    session: SessionDep, actor: ActorDep, intent_id: uuid.UUID, body: CancelBody
) -> dict[str, Any]:
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
    return _snapshot(snapshot)
