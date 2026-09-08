"""Revalidation: the final deterministic gate before privileged execution.

Order follows LLD §9: claim → digest → approval TTL/authorization → current
policy → live target preconditions → permit. Every permit references the exact
digest and a stable operation key; no client can submit a permit.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from hitl_ops.adapters.base import LiveTargetQuery, TargetSnapshot
from hitl_ops.domain.enums import (
    ApprovalDecision,
    ApprovalRoute,
    IntentState,
    PolicyDisposition,
    ToolName,
)
from hitl_ops.domain.errors import NotFoundError, StateConflictError
from hitl_ops.domain.policy import PolicyBundle, evaluate_policy
from hitl_ops.domain.risk import RiskContext, evaluate_risk
from hitl_ops.domain.state_machine import require_transition
from hitl_ops.infrastructure.authorization import (
    load_assignments,
)
from hitl_ops.infrastructure.identity import evaluate_current_roles
from hitl_ops.infrastructure.orm import (
    ActionIntentORM,
    ApprovalDecisionORM,
    ExecutionORM,
    OutboxMessageORM,
    PolicyEvaluationORM,
    StateTransitionORM,
)

DEFAULT_CLAIM_LEASE_SECONDS = 60


@dataclass(frozen=True, slots=True)
class ExecutionPermit:
    """Short-lived process-local data; never a bearer credential."""

    tenant_id: str
    intent_id: uuid.UUID
    revision: int
    intent_digest: str
    tool: str
    typed_parameters: dict[str, Any]
    operation_key: str
    precondition_token: str
    execution_id: uuid.UUID
    issued_at: datetime


@dataclass(frozen=True, slots=True)
class RevalidationFailure:
    state: IntentState
    reason_code: str
    execution_id: uuid.UUID


def operation_key_for(tenant_id: str, intent_id: uuid.UUID, revision: int) -> str:
    return f"{tenant_id}:{intent_id}:{revision}"


def recompute_digest(intent: ActionIntentORM) -> str:
    """Recompute the digest from stored canonical identity and parameters."""

    envelope = {
        "digest_version": "1",
        "tenant_id": intent.tenant_id,
        "requester_id": intent.requester_id,
        "tool": intent.tool,
        "revision": intent.revision,
        "parameters": intent.canonical_parameters,
    }
    canonical = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_stricter(current: str, original: str) -> bool:
    order = [
        PolicyDisposition.ALLOW.value,
        PolicyDisposition.REQUIRE_APPROVAL.value,
        PolicyDisposition.BLOCK.value,
    ]
    return order.index(current) > order.index(original)


def route_stricter(current: ApprovalRoute, original: ApprovalRoute) -> bool:
    order = [ApprovalRoute.NONE, ApprovalRoute.SINGLE, ApprovalRoute.CRITICAL_TWO_STEP]
    return order.index(current) > order.index(original)


def check_preconditions(
    tool: str, parameters: dict[str, Any], snapshot: TargetSnapshot
) -> str | None:
    """Deterministic precondition comparison; returns a failure reason or None."""

    if not snapshot.found:
        return "target_missing"
    if snapshot.health == "degraded":
        return "target_degraded"
    service = parameters.get("service")
    if service is not None and snapshot.identity.get("service") not in (None, service):
        return "target_replaced"
    resource_id = parameters.get("resource_id")
    if resource_id is not None and snapshot.identity.get("resource_id") not in (None, resource_id):
        return "target_replaced"
    name = parameters.get("name")
    if name is not None and snapshot.identity.get("name") not in (None, name):
        return "target_replaced"
    return None


async def _record_transition(
    session: AsyncSession,
    intent: ActionIntentORM,
    target: IntentState,
    *,
    actor_id: str,
    command_id: str,
    reason_code: str,
    db_now: datetime,
) -> None:
    require_transition(IntentState(intent.state), target)
    sequence = (
        await session.execute(
            select(func.max(StateTransitionORM.sequence)).where(
                StateTransitionORM.tenant_id == intent.tenant_id,
                StateTransitionORM.intent_id == intent.intent_id,
                StateTransitionORM.intent_revision == intent.revision,
            )
        )
    ).scalar_one()
    next_sequence = int(sequence or 0) + 1
    session.add(
        StateTransitionORM(
            tenant_id=intent.tenant_id,
            intent_id=intent.intent_id,
            intent_revision=intent.revision,
            from_state=intent.state,
            to_state=target.value,
            actor_type="system",
            actor_id=actor_id,
            command_id=f"{command_id}:{target.value.lower()}:{next_sequence}",
            reason_code=reason_code,
            metadata_={},
            sequence=next_sequence,
            occurred_at=db_now,
        )
    )
    session.add(_outbox(intent, target, reason_code, actor_id, command_id))
    intent.state = target.value
    intent.state_version += 1


def _outbox(
    intent: ActionIntentORM, target: IntentState, reason_code: str, actor_id: str, command_id: str
) -> OutboxMessageORM:
    return OutboxMessageORM(
        topic="intent.transitioned",
        payload={
            "tenant_id": intent.tenant_id,
            "intent_id": str(intent.intent_id),
            "revision": intent.revision,
            "from_state": intent.state,
            "to_state": target.value,
            "reason_code": reason_code,
            "actor_id": actor_id,
            "command_id": command_id,
        },
    )


class RevalidationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_and_revalidate(
        self,
        *,
        tenant_id: str,
        intent_id: uuid.UUID,
        revision: int,
        worker_id: str,
        command_id: str,
        target_query: LiveTargetQuery,
        bundle: PolicyBundle,
        lease_seconds: int = DEFAULT_CLAIM_LEASE_SECONDS,
    ) -> ExecutionPermit | RevalidationFailure:
        try:
            return await self._claim_and_revalidate(
                tenant_id=tenant_id,
                intent_id=intent_id,
                revision=revision,
                worker_id=worker_id,
                command_id=command_id,
                target_query=target_query,
                bundle=bundle,
                lease_seconds=lease_seconds,
            )
        except DBAPIError as exc:
            translated = type(getattr(exc, "orig", None)).__name__
            detail = str(getattr(exc, "orig", ""))
            conflict = (
                translated
                in (
                    "LockNotAvailableError",
                    "SerializationFailureError",
                    "QueryCanceledError",
                    "DeadlockDetectedError",
                )
                or "LockNotAvailableError" in detail
                or "could not obtain lock" in detail.lower()
                or "LockNotAvailableError" in str(exc)
            )
            if conflict:
                raise StateConflictError("row lock or serialization conflict; retry") from exc
            raise

    async def _claim_and_revalidate(
        self,
        *,
        tenant_id: str,
        intent_id: uuid.UUID,
        revision: int,
        worker_id: str,
        command_id: str,
        target_query: LiveTargetQuery,
        bundle: PolicyBundle,
        lease_seconds: int,
    ) -> ExecutionPermit | RevalidationFailure:
        row = (
            await self._session.execute(
                select(ActionIntentORM, func.now())
                .where(
                    ActionIntentORM.tenant_id == tenant_id,
                    ActionIntentORM.intent_id == intent_id,
                    ActionIntentORM.revision == revision,
                )
                .with_for_update(nowait=True)
            )
        ).first()
        if row is None:
            raise NotFoundError("intent revision not found")
        intent, db_now = row
        current = IntentState(intent.state)
        if current not in (IntentState.AUTO_APPROVED, IntentState.APPROVED):
            raise StateConflictError(f"intent is not executable in state {current.value}")

        operation_key = operation_key_for(tenant_id, intent_id, revision)
        existing_execution = (
            await self._session.execute(
                select(ExecutionORM).where(
                    ExecutionORM.intent_id == intent_id, ExecutionORM.intent_revision == revision
                )
            )
        ).scalar_one_or_none()
        if existing_execution is not None:
            if (
                existing_execution.status == "CLAIMED"
                and existing_execution.claim_expires_at is not None
                and existing_execution.claim_expires_at > db_now
            ):
                raise StateConflictError("execution already claimed by another worker")
            # Expired lease: retake the same claim row; still at most one permit.
            existing_execution.attempt += 1
            existing_execution.status = "CLAIMED"
            existing_execution.claim_expires_at = db_now + timedelta(seconds=lease_seconds)
            existing_execution.error_code = None
            execution = existing_execution
        else:
            execution = ExecutionORM(
                tenant_id=tenant_id,
                intent_id=intent_id,
                intent_revision=revision,
                operation_key=operation_key,
                attempt=1,
                status="CLAIMED",
                claim_expires_at=db_now + timedelta(seconds=lease_seconds),
            )
            self._session.add(execution)

        await _record_transition(
            self._session,
            intent,
            IntentState.REVALIDATING,
            actor_id=worker_id,
            command_id=command_id,
            reason_code="execution_claimed",
            db_now=db_now,
        )
        await self._session.flush()

        failure = await self._revalidate_gates(
            intent, execution, db_now, bundle, target_query, worker_id, command_id
        )
        if failure is not None:
            await _record_transition(
                self._session,
                intent,
                failure.state,
                actor_id=worker_id,
                command_id=command_id,
                reason_code=failure.reason_code,
                db_now=db_now,
            )
            execution.status = "FAILED"
            execution.error_code = failure.reason_code
            execution.finished_at = db_now
            await self._session.flush()
            return failure

        await _record_transition(
            self._session,
            intent,
            IntentState.EXECUTING,
            actor_id=worker_id,
            command_id=command_id,
            reason_code="revalidation_passed",
            db_now=db_now,
        )
        execution.status = "EXECUTING"
        execution.started_at = db_now
        await self._session.flush()
        precondition_token = hashlib.sha256(
            json.dumps(
                execution.precondition_snapshot, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
        ).hexdigest()
        return ExecutionPermit(
            tenant_id=tenant_id,
            intent_id=intent_id,
            revision=revision,
            intent_digest=intent.intent_digest,
            tool=intent.tool,
            typed_parameters=intent.canonical_parameters,
            operation_key=operation_key,
            precondition_token=precondition_token,
            execution_id=execution.id,
            issued_at=db_now,
        )

    async def _revalidate_gates(
        self,
        intent: ActionIntentORM,
        execution: ExecutionORM,
        db_now: datetime,
        bundle: PolicyBundle,
        target_query: LiveTargetQuery,
        worker_id: str,
        command_id: str,
    ) -> RevalidationFailure | None:
        def fail(state: IntentState, reason_code: str) -> RevalidationFailure:
            return RevalidationFailure(
                state=state, reason_code=reason_code, execution_id=execution.id
            )

        # Gate 1: digest recomputation from stored canonical data.
        if recompute_digest(intent) != intent.intent_digest:
            return fail(IntentState.STALE, "digest_mismatch")

        # Gate 2: approval TTL and current approver authorization.
        policy_row = (
            await self._session.execute(
                select(PolicyEvaluationORM).where(
                    PolicyEvaluationORM.tenant_id == intent.tenant_id,
                    PolicyEvaluationORM.intent_id == intent.intent_id,
                    PolicyEvaluationORM.intent_revision == intent.revision,
                    PolicyEvaluationORM.generation == 1,
                )
            )
        ).scalar_one()
        route = ApprovalRoute(policy_row.route)

        if intent.approval_expires_at is not None and intent.approval_expires_at <= db_now:
            return fail(IntentState.EXPIRED, "approval_ttl_elapsed")

        if route in (ApprovalRoute.SINGLE, ApprovalRoute.CRITICAL_TWO_STEP):
            decisions = (
                (
                    await self._session.execute(
                        select(ApprovalDecisionORM).where(
                            ApprovalDecisionORM.tenant_id == intent.tenant_id,
                            ApprovalDecisionORM.intent_id == intent.intent_id,
                            ApprovalDecisionORM.intent_revision == intent.revision,
                            ApprovalDecisionORM.decision == ApprovalDecision.APPROVE.value,
                        )
                    )
                )
                .scalars()
                .all()
            )
            current_authorized = 0
            for decision in decisions:
                assignments = await load_assignments(
                    self._session, intent.tenant_id, decision.actor_id
                )
                roles = evaluate_current_roles(
                    assignments, datetime.now(UTC), intent.canonical_parameters.get("environment")
                )
                if any(role in roles for role in policy_row.required_roles):
                    current_authorized += 1
            required = 2 if route is ApprovalRoute.CRITICAL_TWO_STEP else 1
            if current_authorized < required:
                return fail(IntentState.STALE, "approver_no_longer_authorized")

        # Gate 3: current policy re-evaluation (stricter result stales).
        tool = ToolName(intent.tool)
        risk = evaluate_risk(tool, intent.canonical_parameters, RiskContext())
        current_policy = evaluate_policy(tool, risk, intent.canonical_parameters, bundle)
        if current_policy.disposition is PolicyDisposition.BLOCK:
            return fail(IntentState.STALE, "policy_now_blocks")
        if (
            is_stricter(current_policy.disposition.value, policy_row.disposition)
            or route_stricter(current_policy.route, route)
        ) and current_policy.disposition is not PolicyDisposition.ALLOW:
            return fail(IntentState.STALE, "policy_route_stricter")
        # Gate 4: live target preconditions (bounded, read-only).
        snapshot = await target_query.fetch(tool, intent.canonical_parameters)
        reason = check_preconditions(tool.value, intent.canonical_parameters, snapshot)
        if reason is not None:
            return fail(IntentState.STALE, reason)
        execution.precondition_snapshot = {
            "identity": snapshot.identity,
            "health": snapshot.health,
            "facts": snapshot.facts,
        }
        return None
