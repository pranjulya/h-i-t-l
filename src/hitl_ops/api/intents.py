"""Intent routes: creation (direct and shared with the agent path), status, events."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from hitl_ops.agent.orchestrator import AgentOrchestrator
from hitl_ops.agent.schemas import parse_tool_parameters
from hitl_ops.api.dependencies import ActorDep, IdempotencyDep, SessionDep
from hitl_ops.application.commands import IdempotencyService
from hitl_ops.domain.enums import ApprovalRoute, IntentSource, IntentState, ToolName
from hitl_ops.domain.errors import DomainError, NotFoundError
from hitl_ops.domain.intent import canonicalize
from hitl_ops.domain.models import CreateIntentCommand
from hitl_ops.domain.policy import evaluate_policy
from hitl_ops.domain.risk import RiskContext, evaluate_risk
from hitl_ops.domain.state_machine import CANCELLABLE_STATES
from hitl_ops.infrastructure.audit import verify_aggregate_chain
from hitl_ops.infrastructure.identity import (
    AuthenticatedActor,
)
from hitl_ops.infrastructure.orm import (
    ActionIntentORM,
    ApprovalDecisionORM,
    AuditEventORM,
    ExecutionORM,
    PolicyEvaluationORM,
    RiskEvaluationORM,
    StateTransitionORM,
)
from hitl_ops.infrastructure.repositories import (
    EvaluationRepository,
    IntentRepository,
    PolicyBundleRepository,
)

router = APIRouter(prefix="/v1")


class CreateIntentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    parameters: dict[str, Any]
    rationale: str = Field(default="", max_length=2000)


async def create_intent_from_proposal(
    session: SessionDep,
    actor: AuthenticatedActor,
    *,
    tool: str,
    parameters: dict[str, Any],
    rationale: str,
    source: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Shared creation flow: validate → canonicalize → persist → evaluate → route."""

    validated = parse_tool_parameters(tool, parameters)
    canonical = canonicalize(validated_tool(tool), actor.tenant_id, actor.actor_id, 1, validated)
    command = CreateIntentCommand(
        tenant_id=actor.tenant_id,
        intent_id=uuid.uuid4(),
        revision=1,
        tool=canonical.tool,
        canonical_parameters=canonical.parameters,
        intent_digest=canonical.digest,
        requester_id=actor.actor_id,
        requester_rationale=rationale,
        source=IntentSource(source.upper()),
        raw_proposal={"tool": tool, "parameters": parameters},
        command_id=uuid.uuid4().hex,
        correlation_id=correlation_id,
        actor_id=actor.actor_id,
    )
    await IntentRepository(session).create(command)
    bundle = await PolicyBundleRepository(session).get_active(actor.tenant_id)
    if bundle is None:
        raise DomainError("no active policy bundle", code="POLICY_BLOCKED", http_status=409)
    risk = evaluate_risk(command.tool, command.canonical_parameters, RiskContext())
    policy = evaluate_policy(command.tool, risk, command.canonical_parameters, bundle)
    recorded = await EvaluationRepository(session).record(
        actor.tenant_id,
        command.intent_id,
        1,
        risk,
        policy,
        command_id=command.command_id,
        correlation_id=command.correlation_id,
    )
    stored = await IntentRepository(session).get(actor.tenant_id, command.intent_id, 1)
    expires_at = stored.approval_expires_at.isoformat() if stored.approval_expires_at else None
    return {
        "intent_id": str(command.intent_id),
        "revision": 1,
        "digest": canonical.digest,
        "risk": {
            "band": risk.band.value,
            "factors": list(risk.factors),
            "rule_version": risk.rule_version,
        },
        "policy": {
            "disposition": policy.disposition.value,
            "route": policy.route.value,
            "policy_version": policy.policy_version,
            "required_roles": list(policy.required_roles),
            "approval_ttl_seconds": policy.approval_ttl_seconds,
        },
        "state": recorded.state.value,
        "state_version": recorded.state_version,
        "approval_requirements": {
            "required_roles": list(policy.required_roles)
            if policy.route in (ApprovalRoute.SINGLE, ApprovalRoute.CRITICAL_TWO_STEP)
            else [],
            "expires_at": expires_at,
        },
        "links": {
            "self": f"/v1/intents/{command.intent_id}",
            "events": f"/v1/intents/{command.intent_id}/events",
        },
    }


def validated_tool(tool: str) -> ToolName:
    return ToolName(tool)


class AgentIntentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=4000)
    context_refs: dict[str, Any] = Field(default_factory=dict)


@router.post("/agent/intents", status_code=201)
async def create_agent_intent(
    request: Request,
    session: SessionDep,
    actor: ActorDep,
    body: AgentIntentBody,
    idempotency_key: IdempotencyDep,
) -> dict[str, Any]:
    orchestrator: AgentOrchestrator = request.app.state.orchestrator
    # Reserve idempotency before the billable provider call: retries and
    # concurrent duplicates must not invoke the model more than once.
    idempotency = IdempotencyService(session)
    reservation = await idempotency.begin(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        scope="agent_intents",
        key=idempotency_key,
        request_payload=body.model_dump(),
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
    proposal = await orchestrator.propose(body.request, body.context_refs)
    response = await create_intent_from_proposal(
        session,
        actor,
        tool=proposal.tool,
        parameters=proposal.parameters.model_dump(mode="json"),
        rationale=proposal.rationale,
        source="AGENT",
        correlation_id=request.state.correlation_id,
    )
    await idempotency.complete(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        scope="agent_intents",
        key=idempotency_key,
        response_status=201,
        response_body=response,
    )
    return response


@router.post("/intents", status_code=201)
async def create_intent(
    request: Request,
    session: SessionDep,
    actor: ActorDep,
    body: CreateIntentBody,
    idempotency_key: IdempotencyDep,
) -> dict[str, Any]:
    idempotency = IdempotencyService(session)
    reservation = await idempotency.begin(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        scope="intents",
        key=idempotency_key,
        request_payload=body.model_dump(),
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
    response = await create_intent_from_proposal(
        session,
        actor,
        tool=body.tool,
        parameters=body.parameters,
        rationale=body.rationale,
        source="DIRECT",
        correlation_id=request.state.correlation_id,
    )
    await idempotency.complete(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        scope="intents",
        key=idempotency_key,
        response_status=201,
        response_body=response,
    )
    return response


@router.get("/intents/{intent_id}")
async def get_intent(session: SessionDep, actor: ActorDep, intent_id: uuid.UUID) -> dict[str, Any]:
    row = (
        await session.execute(
            select(ActionIntentORM)
            .where(
                ActionIntentORM.tenant_id == actor.tenant_id,
                ActionIntentORM.intent_id == intent_id,
            )
            .order_by(ActionIntentORM.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("intent not found")

    risk_row = (
        await session.execute(
            select(RiskEvaluationORM).where(
                RiskEvaluationORM.tenant_id == actor.tenant_id,
                RiskEvaluationORM.intent_id == intent_id,
                RiskEvaluationORM.intent_revision == row.revision,
                RiskEvaluationORM.generation == 1,
            )
        )
    ).scalar_one_or_none()
    policy_row = (
        await session.execute(
            select(PolicyEvaluationORM).where(
                PolicyEvaluationORM.tenant_id == actor.tenant_id,
                PolicyEvaluationORM.intent_id == intent_id,
                PolicyEvaluationORM.intent_revision == row.revision,
                PolicyEvaluationORM.generation == 1,
            )
        )
    ).scalar_one_or_none()
    approvals = (
        (
            await session.execute(
                select(ApprovalDecisionORM)
                .where(
                    ApprovalDecisionORM.tenant_id == actor.tenant_id,
                    ApprovalDecisionORM.intent_id == intent_id,
                    ApprovalDecisionORM.intent_revision == row.revision,
                )
                .order_by(ApprovalDecisionORM.decided_at)
            )
        )
        .scalars()
        .all()
    )
    execution = (
        (
            await session.execute(
                select(ExecutionORM).where(
                    ExecutionORM.tenant_id == actor.tenant_id,
                    ExecutionORM.intent_id == intent_id,
                    ExecutionORM.intent_revision == row.revision,
                )
            )
        )
        .scalars()
        .first()
    )

    state = IntentState(row.state)
    permitted_actions: list[str] = []
    if state in CANCELLABLE_STATES:
        permitted_actions.append("cancel")
    if state in (
        IntentState.PENDING_APPROVAL_1,
        IntentState.PENDING_APPROVAL_2,
        IntentState.APPROVED_BY_LEVEL_1,
    ):
        permitted_actions.append("decide_approval")

    return {
        "intent_id": str(row.intent_id),
        "revision": row.revision,
        "tool": row.tool,
        "digest": row.intent_digest,
        "state": row.state,
        "state_version": row.state_version,
        "approval_expires_at": row.approval_expires_at.isoformat()
        if row.approval_expires_at
        else None,
        "risk": {
            "band": risk_row.band,
            "factors": list(risk_row.factors),
            "rule_version": risk_row.rule_version,
        }
        if risk_row
        else None,
        "policy": {
            "disposition": policy_row.disposition,
            "route": policy_row.route,
            "policy_version": policy_row.policy_version,
            "required_roles": list(policy_row.required_roles),
            "approval_ttl_seconds": policy_row.approval_ttl_seconds,
        }
        if policy_row
        else None,
        "approvals": [
            {
                "level": d.level,
                "decision": d.decision,
                "actor_id": d.actor_id,
                "reason": d.reason,
                "decided_at": d.decided_at.isoformat(),
            }
            for d in approvals
        ],
        "execution": {
            "status": execution.status,
            "provider_operation_id": execution.provider_operation_id,
            "error_code": execution.error_code,
        }
        if execution
        else None,
        "permitted_actions": permitted_actions,
    }


@router.get("/intents/{intent_id}/events")
async def get_events(
    session: SessionDep,
    actor: ActorDep,
    intent_id: uuid.UUID,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    exists = (
        await session.execute(
            select(ActionIntentORM.intent_id).where(
                ActionIntentORM.tenant_id == actor.tenant_id,
                ActionIntentORM.intent_id == intent_id,
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        raise NotFoundError("intent not found")
    rows = (
        (
            await session.execute(
                select(StateTransitionORM)
                .where(
                    StateTransitionORM.tenant_id == actor.tenant_id,
                    StateTransitionORM.intent_id == intent_id,
                    StateTransitionORM.sequence > cursor,
                )
                .order_by(StateTransitionORM.sequence)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    items = [
        {
            "sequence": r.sequence,
            "from_state": r.from_state,
            "to_state": r.to_state,
            "reason_code": r.reason_code,
            "actor_type": r.actor_type,
            "occurred_at": r.occurred_at.isoformat(),
        }
        for r in rows
    ]
    next_cursor = items[-1]["sequence"] if len(items) == limit else None

    audit_rows = (
        (
            await session.execute(
                select(AuditEventORM)
                .where(
                    AuditEventORM.tenant_id == actor.tenant_id,
                    AuditEventORM.aggregate_id == intent_id,
                )
                .order_by(AuditEventORM.sequence)
            )
        )
        .scalars()
        .all()
    )
    chain_valid, chain_problem = await verify_aggregate_chain(session, actor.tenant_id, intent_id)
    audit = [
        {
            "sequence": a.sequence,
            "event_type": a.event_type,
            "actor_id": a.actor_id,
            "occurred_at": a.occurred_at.isoformat(),
            "previous_hash": a.previous_hash,
            "event_hash": a.event_hash,
        }
        for a in audit_rows
    ]
    return {
        "events": items,
        "next_cursor": next_cursor,
        "audit": audit,
        "chain_valid": chain_valid,
        "chain_problem": chain_problem,
    }
