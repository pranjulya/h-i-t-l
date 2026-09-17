"""Reconciliation: resolve EXECUTION_UNKNOWN with provider evidence only.

Reconciliation is not retry: it maps provider evidence to a final outcome and
preserves UNKNOWN when evidence is insufficient, flagging operator escalation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hitl_ops.adapters.base import AdapterResult
from hitl_ops.domain.enums import ExecutionOutcome, IntentState, ToolName
from hitl_ops.domain.errors import NotFoundError, StateConflictError
from hitl_ops.domain.intent import sanitize_raw_proposal
from hitl_ops.domain.state_machine import require_transition
from hitl_ops.infrastructure.orm import (
    ActionIntentORM,
    ExecutionORM,
    OutboxMessageORM,
    StateTransitionORM,
)

MAX_RECONCILE_ATTEMPTS = 5
RECONCILE_BACKOFF_BASE_SECONDS = 5
RECONCILE_BACKOFF_CEILING_SECONDS = 300


def reconcile_backoff_seconds(attempts: int) -> int:
    """Exponential backoff between automatic reconciliation attempts."""

    if attempts < 1:
        return RECONCILE_BACKOFF_BASE_SECONDS
    backoff: int = RECONCILE_BACKOFF_BASE_SECONDS * 2 ** (attempts - 1)
    return min(backoff, RECONCILE_BACKOFF_CEILING_SECONDS)


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    execution_id: uuid.UUID
    outcome: ExecutionOutcome
    state: IntentState
    escalated: bool
    summary: dict[str, Any]


class ReconciliationService:
    def __init__(self, session: AsyncSession, adapter: Any) -> None:
        self._session = session
        self._adapter = adapter

    async def reconcile(
        self, *, execution_id: uuid.UUID, worker_id: str, command_id: str
    ) -> ReconciliationResult:
        execution = (
            await self._session.execute(select(ExecutionORM).where(ExecutionORM.id == execution_id))
        ).scalar_one_or_none()
        if execution is None:
            raise NotFoundError("execution not found")
        row = (
            await self._session.execute(
                select(ActionIntentORM)
                .where(
                    ActionIntentORM.tenant_id == execution.tenant_id,
                    ActionIntentORM.intent_id == execution.intent_id,
                    ActionIntentORM.revision == execution.intent_revision,
                )
                .with_for_update()
            )
        ).scalar_one()
        if IntentState(row.state) is not IntentState.EXECUTION_UNKNOWN:
            raise StateConflictError(f"execution is not awaiting reconciliation: {row.state}")

        provider_error: Exception | None = None
        try:
            evidence: AdapterResult = await self._adapter.lookup_status(
                ToolName(row.tool), execution.operation_key, execution.provider_operation_id
            )
        except Exception as exc:
            provider_error = exc
            evidence = AdapterResult(
                outcome=ExecutionOutcome.UNKNOWN.value,
                provider_operation_id=execution.provider_operation_id,
                summary={"evidence": "provider_unavailable"},
            )

        db_now = datetime.now(UTC)
        if provider_error is not None or evidence.outcome == ExecutionOutcome.UNKNOWN.value:
            # Evidence is insufficient: UNKNOWN is preserved, never guessed.
            # Automatic reconciliation is scheduled with backoff and stops
            # entirely once the attempt budget is spent, handing the outcome
            # to an operator instead of polling an unhealthy provider forever.
            attempts = (execution.reconcile_attempts or 0) + 1
            execution.reconcile_attempts = attempts
            execution.status = "UNKNOWN"
            exhausted = attempts >= MAX_RECONCILE_ATTEMPTS
            execution.next_reconcile_at = (
                None
                if exhausted
                else db_now + timedelta(seconds=reconcile_backoff_seconds(attempts))
            )
            execution.result_summary = sanitize_raw_proposal(
                {
                    "escalated": True,
                    "evidence": evidence.summary,
                    "reconcile_attempts": attempts,
                    "reconcile_exhausted": exhausted,
                }
            )
            if exhausted:
                execution.error_code = "reconcile_attempts_exhausted"
                self._session.add(
                    OutboxMessageORM(
                        topic="execution.reconciliation_exhausted",
                        payload={
                            "tenant_id": execution.tenant_id,
                            "intent_id": str(execution.intent_id),
                            "revision": execution.intent_revision,
                            "attempts": attempts,
                            "command_id": command_id,
                        },
                    )
                )
            await self._session.flush()
            return ReconciliationResult(
                execution_id=execution.id,
                outcome=ExecutionOutcome.UNKNOWN,
                state=IntentState.EXECUTION_UNKNOWN,
                escalated=True,
                summary=execution.result_summary or {},
            )

        outcome = ExecutionOutcome(evidence.outcome)
        target_state = (
            IntentState.SUCCEEDED if outcome is ExecutionOutcome.SUCCEEDED else IntentState.FAILED
        )
        await self._record_transition(
            row,
            target_state,
            worker_id=worker_id,
            command_id=command_id,
            reason_code=f"reconciled_{outcome.value.lower()}",
            db_now=db_now,
        )
        execution.status = outcome.value
        execution.provider_operation_id = (
            evidence.provider_operation_id or execution.provider_operation_id
        )
        execution.finished_at = db_now
        execution.result_summary = sanitize_raw_proposal(evidence.summary)
        execution.next_reconcile_at = None
        execution.error_code = None
        await self._session.flush()
        return ReconciliationResult(
            execution_id=execution.id,
            outcome=outcome,
            state=target_state,
            escalated=False,
            summary=execution.result_summary or {},
        )

    async def _record_transition(
        self,
        intent: ActionIntentORM,
        target: IntentState,
        *,
        worker_id: str,
        command_id: str,
        reason_code: str,
        db_now: datetime,
    ) -> None:
        require_transition(IntentState(intent.state), target)
        sequence = (
            await self._session.execute(
                select(func.max(StateTransitionORM.sequence)).where(
                    StateTransitionORM.tenant_id == intent.tenant_id,
                    StateTransitionORM.intent_id == intent.intent_id,
                    StateTransitionORM.intent_revision == intent.revision,
                )
            )
        ).scalar_one()
        next_sequence = int(sequence or 0) + 1
        self._session.add(
            StateTransitionORM(
                tenant_id=intent.tenant_id,
                intent_id=intent.intent_id,
                intent_revision=intent.revision,
                from_state=intent.state,
                to_state=target.value,
                actor_type="system",
                actor_id=worker_id,
                command_id=f"{command_id}:{target.value.lower()}:{next_sequence}",
                reason_code=reason_code,
                metadata_={},
                sequence=next_sequence,
                occurred_at=db_now,
            )
        )
        self._session.add(
            OutboxMessageORM(
                topic="execution.reconciled",
                payload={
                    "tenant_id": intent.tenant_id,
                    "intent_id": str(intent.intent_id),
                    "revision": intent.revision,
                    "to_state": target.value,
                    "command_id": command_id,
                },
            )
        )
        intent.state = target.value
        intent.state_version += 1
