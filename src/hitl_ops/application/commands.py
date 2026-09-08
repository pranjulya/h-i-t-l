"""Approval command handlers: decisions, cancellation, and expiry.

Every handler loads and locks the intent row with an expected state version,
validates guards (digest binding, TTL, current authorization, distinctness),
appends append-only evidence, applies only legal transitions from the
authoritative state table, and writes a same-transaction audit outbox row for
every committed transition.

No service method commits or rolls back: callers own the transaction, and the
service only flushes. On the expiry path the caller must commit (not roll back)
so the durable EXPIRED transition persists; retries then observe EXPIRED state.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from hitl_ops.domain.enums import ApprovalDecision, ApprovalRoute, IntentState
from hitl_ops.domain.errors import (
    ApprovalExpiredError,
    ApprovalStaleError,
    ForbiddenError,
    IdempotencyConflictError,
    IllegalTransitionError,
    NotFoundError,
    StateConflictError,
)
from hitl_ops.domain.models import IntentSnapshot
from hitl_ops.domain.state_machine import CANCELLABLE_STATES, require_transition
from hitl_ops.infrastructure.authorization import current_roles
from hitl_ops.infrastructure.identity import AuthenticatedActor
from hitl_ops.infrastructure.orm import (
    ActionIntentORM,
    ApprovalDecisionORM,
    IdempotencyRecordORM,
    OutboxMessageORM,
    PolicyEvaluationORM,
    StateTransitionORM,
)


@dataclass(frozen=True, slots=True)
class ApprovalDecisionCommand:
    tenant_id: str
    intent_id: uuid.UUID
    revision: int
    intent_digest: str
    level: int
    decision: ApprovalDecision
    reason: str
    expected_state_version: int
    actor: AuthenticatedActor
    command_id: str
    obligations: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CancelIntentCommand:
    tenant_id: str
    intent_id: uuid.UUID
    revision: int
    reason: str
    expected_state_version: int
    actor: AuthenticatedActor
    command_id: str


def _snapshot(intent: ActionIntentORM) -> IntentSnapshot:
    return IntentSnapshot(
        tenant_id=intent.tenant_id,
        intent_id=intent.intent_id,
        revision=intent.revision,
        tool=intent.tool,  # type: ignore[arg-type]
        intent_digest=intent.intent_digest,
        state=IntentState(intent.state),
        state_version=intent.state_version,
        created_at=intent.created_at,
    )


async def _load_locked_intent(
    session: AsyncSession, tenant_id: str, intent_id: uuid.UUID, revision: int
) -> tuple[ActionIntentORM, datetime]:
    try:
        row = (
            await session.execute(
                select(ActionIntentORM, func.now())
                .where(
                    ActionIntentORM.tenant_id == tenant_id,
                    ActionIntentORM.intent_id == intent_id,
                    ActionIntentORM.revision == revision,
                )
                .with_for_update(nowait=True)
            )
        ).first()
    except (OperationalError, DBAPIError) as exc:
        raise StateConflictError("intent row is locked by another command") from exc
    except Exception as exc:
        if type(exc).__name__ in ("LockNotAvailableError", "QueryCanceledError"):
            raise StateConflictError("intent row is locked by another command") from exc
        raise
    if row is None:
        raise NotFoundError("intent revision not found")
    intent, db_now = row
    return intent, db_now


def _required_role_for_level(
    route: ApprovalRoute, level: int, required_roles: tuple[str, ...]
) -> str | None:
    """Bind the approval level to its specific role.

    Critical two-step routes require ``critical_approver_l1`` for L1 and
    ``critical_approver_l2`` for L2; a single-step route requires its sole
    approval role for level 1. This prevents two differently named roles from
    approving in the wrong level order.
    """

    if route is ApprovalRoute.CRITICAL_TWO_STEP:
        index = level - 1
        return required_roles[index] if 0 <= index < len(required_roles) else None
    if level == 1 and required_roles:
        return required_roles[0]
    return None


async def _next_sequence(session: AsyncSession, intent: ActionIntentORM) -> int:
    current = (
        await session.execute(
            select(func.max(StateTransitionORM.sequence)).where(
                StateTransitionORM.tenant_id == intent.tenant_id,
                StateTransitionORM.intent_id == intent.intent_id,
                StateTransitionORM.intent_revision == intent.revision,
            )
        )
    ).scalar_one()
    return int(current or 0) + 1


async def _record_transition(
    session: AsyncSession,
    intent: ActionIntentORM,
    target: IntentState,
    *,
    actor_type: str,
    actor_id: str,
    command_id: str,
    reason_code: str,
    db_now: datetime,
) -> None:
    require_transition(IntentState(intent.state), target)
    sequence = await _next_sequence(session, intent)
    session.add(
        StateTransitionORM(
            tenant_id=intent.tenant_id,
            intent_id=intent.intent_id,
            intent_revision=intent.revision,
            from_state=intent.state,
            to_state=target.value,
            actor_type=actor_type,
            actor_id=actor_id,
            command_id=f"{command_id}:{target.value.lower()}:{sequence}",
            reason_code=reason_code,
            metadata_={},
            sequence=sequence,
            occurred_at=db_now,
        )
    )
    session.add(
        OutboxMessageORM(
            topic="intent.transitioned",
            payload={
                "tenant_id": intent.tenant_id,
                "intent_id": str(intent.intent_id),
                "revision": intent.revision,
                "from_state": intent.state,
                "to_state": target.value,
                "actor_id": actor_id,
                "command_id": command_id,
            },
        )
    )
    intent.state = target.value
    intent.state_version += 1


class ApprovalCommandService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def decide(self, command: ApprovalDecisionCommand) -> IntentSnapshot:
        if command.tenant_id != command.actor.tenant_id:
            raise NotFoundError("intent revision not found")
        intent, db_now = await _load_locked_intent(
            self._session, command.tenant_id, command.intent_id, command.revision
        )
        if intent.state_version != command.expected_state_version:
            raise StateConflictError("expected state version does not match")
        if intent.intent_digest != command.intent_digest:
            raise ApprovalStaleError("decision digest does not match the current revision")

        current = IntentState(intent.state)
        expired = (
            current
            in (
                IntentState.PENDING_APPROVAL_1,
                IntentState.APPROVED_BY_LEVEL_1,
                IntentState.PENDING_APPROVAL_2,
                IntentState.APPROVED,
            )
            and intent.approval_expires_at is not None
            and intent.approval_expires_at <= db_now
        )
        if expired:
            # The caller owns the transaction. Persist EXPIRED so the caller's
            # commit durably records it, then raise: the error response carries
            # no new command state, and the durable EXPIRED row is what a retry
            # observes. Callers must commit on this path, not roll back.
            await _record_transition(
                self._session,
                intent,
                IntentState.EXPIRED,
                actor_type="system",
                actor_id="approval-clock",
                command_id=command.command_id,
                reason_code="approval_ttl_elapsed",
                db_now=db_now,
            )
            await self._session.flush()
            raise ApprovalExpiredError("approval window elapsed")

        policy_row = (
            await self._session.execute(
                select(PolicyEvaluationORM).where(
                    PolicyEvaluationORM.tenant_id == command.tenant_id,
                    PolicyEvaluationORM.intent_id == command.intent_id,
                    PolicyEvaluationORM.intent_revision == command.revision,
                    PolicyEvaluationORM.generation == 1,
                )
            )
        ).scalar_one()
        route = ApprovalRoute(policy_row.route)
        environment = intent.canonical_parameters.get("environment")
        roles = await current_roles(self._session, command.actor, environment)
        required_roles = tuple(policy_row.required_roles)
        if required_roles:
            level_role = _required_role_for_level(route, command.level, required_roles)
            if level_role is None or level_role not in roles:
                raise ForbiddenError("actor does not hold the role required for this approval level")

        if command.decision is ApprovalDecision.APPROVE:
            self._enforce_scopes(command.actor.scopes, policy_row.required_scopes)
            self._enforce_obligations(command.obligations, policy_row.obligations)

        if (
            command.decision is ApprovalDecision.APPROVE
            and command.actor.actor_id == intent.requester_id
        ):
            raise ForbiddenError("requester cannot approve their own intent")

        target = await self._resolve_target(intent, route, command)
        actor_roles = sorted(roles)
        self._session.add(
            ApprovalDecisionORM(
                tenant_id=command.tenant_id,
                intent_id=command.intent_id,
                intent_revision=command.revision,
                intent_digest=command.intent_digest,
                level=command.level,
                decision=command.decision.value,
                actor_id=command.actor.actor_id,
                actor_roles_snapshot=actor_roles,
                scope_snapshot={
                    "environment": environment,
                    "scopes": sorted(command.actor.scopes),
                    "obligations": command.obligations,
                },
                reason=command.reason,
                policy_version=policy_row.policy_version,
                decided_at=db_now,
                expires_at=intent.approval_expires_at,
            )
        )
        await _record_transition(
            self._session,
            intent,
            target,
            actor_type="human",
            actor_id=command.actor.actor_id,
            command_id=command.command_id,
            reason_code=f"approval_{command.decision.value.lower()}",
            db_now=db_now,
        )
        if (
            command.decision is ApprovalDecision.APPROVE
            and target is IntentState.APPROVED_BY_LEVEL_1
        ):
            await _record_transition(
                self._session,
                intent,
                IntentState.PENDING_APPROVAL_2,
                actor_type="system",
                actor_id="approval-coordinator",
                command_id=command.command_id,
                reason_code="level_2_request_published",
                db_now=db_now,
            )
        await self._session.flush()
        return _snapshot(intent)

    def _enforce_scopes(self, actor_scopes: frozenset[str], required_scopes: list[str]) -> None:
        missing = [scope for scope in required_scopes if scope not in actor_scopes]
        if missing:
            raise ForbiddenError(
                f"actor does not hold required scopes: {', '.join(sorted(missing))}"
            )

    def _enforce_obligations(
        self, provided: dict[str, str], required_obligations: list[str]
    ) -> None:
        missing = [name for name in required_obligations if not provided.get(name)]
        if missing:
            raise ForbiddenError(
                f"mandatory obligations are not satisfied: {', '.join(sorted(missing))}"
            )

    async def _resolve_target(
        self, intent: ActionIntentORM, route: ApprovalRoute, command: ApprovalDecisionCommand
    ) -> IntentState:
        current = IntentState(intent.state)
        if command.decision is ApprovalDecision.REJECT:
            require_transition(current, IntentState.REJECTED)
            return IntentState.REJECTED
        if current is IntentState.PENDING_APPROVAL_1:
            if command.level != 1:
                raise IllegalTransitionError("level 1 decision is required in PENDING_APPROVAL_1")
            if route is ApprovalRoute.CRITICAL_TWO_STEP:
                return IntentState.APPROVED_BY_LEVEL_1
            return IntentState.APPROVED
        if current is IntentState.PENDING_APPROVAL_2:
            if command.level != 2:
                raise IllegalTransitionError("level 2 decision is required in PENDING_APPROVAL_2")
            if route is not ApprovalRoute.CRITICAL_TWO_STEP:
                raise IllegalTransitionError("intent is not routed for critical two-step approval")
            await self._enforce_distinct_l1(intent, command.actor.actor_id)
            return IntentState.APPROVED
        raise IllegalTransitionError(f"decision is not accepted in state {current.value}")

    async def _enforce_distinct_l1(self, intent: ActionIntentORM, actor_id: str) -> None:
        l1_actor = (
            await self._session.execute(
                select(ApprovalDecisionORM.actor_id).where(
                    ApprovalDecisionORM.tenant_id == intent.tenant_id,
                    ApprovalDecisionORM.intent_id == intent.intent_id,
                    ApprovalDecisionORM.intent_revision == intent.revision,
                    ApprovalDecisionORM.level == 1,
                    ApprovalDecisionORM.decision == "APPROVE",
                )
            )
        ).scalar_one_or_none()
        if l1_actor is not None and l1_actor == actor_id:
            raise ForbiddenError("L2 approver must differ from the L1 approver")

    async def cancel(self, command: CancelIntentCommand) -> IntentSnapshot:
        if command.tenant_id != command.actor.tenant_id:
            raise NotFoundError("intent revision not found")
        intent, db_now = await _load_locked_intent(
            self._session, command.tenant_id, command.intent_id, command.revision
        )
        if intent.state_version != command.expected_state_version:
            raise StateConflictError("expected state version does not match")
        current = IntentState(intent.state)
        if current not in CANCELLABLE_STATES:
            raise IllegalTransitionError(f"cancellation is not accepted in state {current.value}")
        if command.actor.actor_id != intent.requester_id:
            roles = await current_roles(self._session, command.actor, None)
            if "administrator" not in roles:
                raise ForbiddenError("only the requester or an administrator may cancel")
        await _record_transition(
            self._session,
            intent,
            IntentState.CANCELLED,
            actor_type="human",
            actor_id=command.actor.actor_id,
            command_id=command.command_id,
            reason_code="cancelled",
            db_now=db_now,
        )
        await self._session.flush()
        return _snapshot(intent)


def canonical_request_hash(payload: dict[str, Any]) -> str:
    """Semantic request identity: key order and whitespace never matter."""

    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def idempotency_key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class IdempotencyReservation:
    replayed: bool
    pending: bool
    response_status: int | None
    response_body: dict[str, Any] | None


class IdempotencyService:
    """Command idempotency over the idempotency_records table.

    Same key + same semantic request replays the original response; same key
    with a changed body is a conflict; a fresh key reserves the command.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def begin(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        scope: str,
        key: str,
        request_payload: dict[str, Any],
        expires_at: datetime | None = None,
    ) -> IdempotencyReservation:
        key_hash = idempotency_key_hash(key)
        request_hash = canonical_request_hash(request_payload)
        existing = await self._lookup(tenant_id, actor_id, scope, key_hash)
        if existing is not None:
            return self._reservation_from(existing, request_hash)

        inserted_id = (
            await self._session.execute(
                insert(IdempotencyRecordORM)
                .values(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    scope=scope,
                    key_hash=key_hash,
                    request_hash=request_hash,
                    status="PENDING",
                    expires_at=expires_at,
                )
                .on_conflict_do_nothing(
                    constraint="uq_idempotency_records_key",
                )
                .returning(IdempotencyRecordORM.id)
            )
        ).scalar_one_or_none()
        if inserted_id is not None:
            return IdempotencyReservation(
                replayed=False, pending=False, response_status=None, response_body=None
            )
        # A concurrent request won the race during the insert; re-read and
        # convert that into the stable contract rather than a raw conflict.
        existing = await self._lookup(tenant_id, actor_id, scope, key_hash)
        if existing is not None:
            return self._reservation_from(existing, request_hash)
        return IdempotencyReservation(
            replayed=False, pending=False, response_status=None, response_body=None
        )

    async def _lookup(
        self, tenant_id: str, actor_id: str, scope: str, key_hash: str
    ) -> IdempotencyRecordORM | None:
        return (
            await self._session.execute(
                select(IdempotencyRecordORM).where(
                    IdempotencyRecordORM.tenant_id == tenant_id,
                    IdempotencyRecordORM.actor_id == actor_id,
                    IdempotencyRecordORM.scope == scope,
                    IdempotencyRecordORM.key_hash == key_hash,
                )
            )
        ).scalar_one_or_none()

    def _reservation_from(
        self, existing: IdempotencyRecordORM, request_hash: str
    ) -> IdempotencyReservation:
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError("idempotency key reused with different request")
        if existing.status == "COMPLETED":
            return IdempotencyReservation(
                replayed=True,
                pending=False,
                response_status=existing.response_status,
                response_body=existing.response_body,
            )
        return IdempotencyReservation(
            replayed=True, pending=True, response_status=None, response_body=None
        )

    async def complete(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        scope: str,
        key: str,
        response_status: int,
        response_body: dict[str, Any],
    ) -> None:
        key_hash = idempotency_key_hash(key)
        existing = (
            await self._session.execute(
                select(IdempotencyRecordORM).where(
                    IdempotencyRecordORM.tenant_id == tenant_id,
                    IdempotencyRecordORM.actor_id == actor_id,
                    IdempotencyRecordORM.scope == scope,
                    IdempotencyRecordORM.key_hash == key_hash,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise NotFoundError("idempotency reservation not found")
        existing.status = "COMPLETED"
        existing.response_status = response_status
        existing.response_body = response_body
        await self._session.flush()
