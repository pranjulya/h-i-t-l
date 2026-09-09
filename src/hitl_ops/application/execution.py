"""Execution service: the only path that turns a permit into adapter calls.

`EXECUTING` is persisted (Phase 04) before the provider send, so a crash after
send leaves evidence that reconciliation can use. Outcome classification never
guesses ambiguity, and results are sanitized before storage.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hitl_ops.adapters.base import AdapterCommand, AdapterPreSendError, AdapterResult
from hitl_ops.application.revalidation import ExecutionPermit
from hitl_ops.domain.enums import ExecutionOutcome, IntentState, ToolName
from hitl_ops.domain.errors import DomainError, StateConflictError
from hitl_ops.domain.intent import sanitize_raw_proposal
from hitl_ops.domain.state_machine import require_transition
from hitl_ops.infrastructure.orm import (
    ActionIntentORM,
    ExecutionORM,
    OutboxMessageORM,
    StateTransitionORM,
)

DEFAULT_ADAPTER_TIMEOUT_SECONDS = 10.0


class ExecutionClassificationError(DomainError):
    code = "EXECUTION_FAILED"
    message = "Execution could not be classified."
    http_status = 500


def classify_outcome(
    result: AdapterResult | None, error: Exception | None
) -> tuple[ExecutionOutcome, str | None]:
    """Deterministic outcome classification; ambiguity is never guessed."""

    if isinstance(error, asyncio.TimeoutError):
        return ExecutionOutcome.UNKNOWN, "adapter_timeout_after_send"
    if isinstance(error, AdapterPreSendError):
        return ExecutionOutcome.FAILED, "adapter_pre_send_rejected"
    if error is not None:
        return ExecutionOutcome.UNKNOWN, "adapter_unavailable"
    if result is None:
        return ExecutionOutcome.UNKNOWN, "no_adapter_result"
    return ExecutionOutcome(result.outcome), result.error_code


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    execution_id: uuid.UUID
    intent_id: uuid.UUID
    revision: int
    state: IntentState
    outcome: ExecutionOutcome
    provider_operation_id: str | None
    error_code: str | None
    summary: dict[str, Any]


_TARGET_STATE: dict[ExecutionOutcome, IntentState] = {
    ExecutionOutcome.SUCCEEDED: IntentState.SUCCEEDED,
    ExecutionOutcome.FAILED: IntentState.FAILED,
    ExecutionOutcome.UNKNOWN: IntentState.EXECUTION_UNKNOWN,
}


class ExecutionService:
    def __init__(self, session: AsyncSession, adapter: Any) -> None:
        self._session = session
        self._adapter = adapter

    async def execute_permit(
        self,
        permit: ExecutionPermit,
        *,
        worker_id: str,
        command_id: str,
        timeout_seconds: float = DEFAULT_ADAPTER_TIMEOUT_SECONDS,
    ) -> ExecutionRecord:
        execution = (
            await self._session.execute(
                select(ExecutionORM).where(ExecutionORM.id == permit.execution_id)
            )
        ).scalar_one()
        row = (
            await self._session.execute(
                select(ActionIntentORM)
                .where(
                    ActionIntentORM.tenant_id == permit.tenant_id,
                    ActionIntentORM.intent_id == permit.intent_id,
                    ActionIntentORM.revision == permit.revision,
                )
                .with_for_update()
            )
        ).scalar_one()
        if IntentState(row.state) is not IntentState.EXECUTING:
            raise StateConflictError(f"intent is not executing: {row.state}")

        result: AdapterResult | None = None
        error: Exception | None = None
        try:
            result = await asyncio.wait_for(
                self._adapter.execute(
                    ToolName(permit.tool),
                    AdapterCommand(
                        operation_key=permit.operation_key,
                        precondition_token=permit.precondition_token,
                        parameters=permit.typed_parameters,
                        resource_version=permit.resource_version,
                    ),
                ),
                timeout=timeout_seconds,
            )
        except (TimeoutError, AdapterPreSendError, Exception) as exc:
            error = exc

        outcome, error_code = classify_outcome(result, error)
        db_now = datetime.now(UTC)
        target_state = _TARGET_STATE[outcome]

        await self._record_transition(
            row,
            target_state,
            worker_id=worker_id,
            command_id=command_id,
            reason_code=error_code or f"execution_{outcome.value.lower()}",
            db_now=db_now,
        )

        execution.status = outcome.value
        execution.provider_operation_id = (
            result.provider_operation_id if result is not None else None
        )
        execution.error_code = error_code
        execution.finished_at = db_now
        if result is not None:
            execution.result_summary = sanitize_raw_proposal(result.summary)
        await self._session.flush()
        return ExecutionRecord(
            execution_id=execution.id,
            intent_id=permit.intent_id,
            revision=permit.revision,
            state=target_state,
            outcome=outcome,
            provider_operation_id=execution.provider_operation_id,
            error_code=error_code,
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
                topic=f"execution.{target.value.lower()}",
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
