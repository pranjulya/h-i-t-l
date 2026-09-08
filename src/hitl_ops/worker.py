"""Worker entry point: claim eligible revisions, execute, reconcile.

Worker identity and network access are separate from API/agent components.
The tick is bounded; a scheduler (compose command or CI job) drives it.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from hitl_ops.adapters.base import DenyingTargetQuery, InfrastructureAdapter
from hitl_ops.application.execution import ExecutionService
from hitl_ops.application.reconciliation import ReconciliationService
from hitl_ops.application.revalidation import RevalidationService
from hitl_ops.domain.enums import IntentState
from hitl_ops.domain.errors import DomainError, StateConflictError
from hitl_ops.infrastructure.orm import ActionIntentORM
from hitl_ops.infrastructure.repositories import PolicyBundleRepository

_EXECUTABLE_STATES = (IntentState.AUTO_APPROVED.value, IntentState.APPROVED.value)
_UNKNOWN_STATES = (IntentState.EXECUTION_UNKNOWN.value,)
_TICK_LIMIT = 5


async def run_worker_tick(session_factory: Any, adapter: InfrastructureAdapter) -> dict[str, int]:
    """One bounded worker pass: execute eligible intents, reconcile unknowns."""

    stats = {"executed": 0, "reconciled": 0, "skipped": 0}

    async with session_factory() as session:
        bundle = await PolicyBundleRepository(session).get_active()
    if bundle is None:
        return stats

    # Snapshot unknowns before executing: reconciliation is a separate pass and
    # must not immediately resolve what this same tick just marked unknown.
    async with session_factory() as session:
        pending_unknowns = (
            await session.execute(
                select(
                    ActionIntentORM.tenant_id, ActionIntentORM.intent_id, ActionIntentORM.revision
                )
                .where(ActionIntentORM.state.in_(_UNKNOWN_STATES))
                .limit(_TICK_LIMIT)
            )
        ).all()

    async with session_factory() as session:
        eligible = (
            await session.execute(
                select(
                    ActionIntentORM.tenant_id, ActionIntentORM.intent_id, ActionIntentORM.revision
                )
                .where(ActionIntentORM.state.in_(_EXECUTABLE_STATES))
                .limit(_TICK_LIMIT)
            )
        ).all()

    for tenant_id, intent_id, revision in eligible:
        command_id = uuid.uuid4().hex
        try:
            async with session_factory() as session, session.begin():
                revalidation = RevalidationService(session)
                permit = await revalidation.claim_and_revalidate(
                    tenant_id=tenant_id,
                    intent_id=intent_id,
                    revision=revision,
                    worker_id="execution-worker",
                    command_id=command_id,
                    target_query=_target_query_for(adapter),
                    bundle=bundle,
                )
                from hitl_ops.application.revalidation import ExecutionPermit

                if not isinstance(permit, ExecutionPermit):
                    stats["skipped"] += 1
                    continue
                executor = ExecutionService(session, adapter)
                await executor.execute_permit(
                    permit, worker_id="execution-worker", command_id=command_id
                )
                stats["executed"] += 1
        except StateConflictError:
            stats["skipped"] += 1

    for tenant_id, intent_id, revision in pending_unknowns:
        command_id = uuid.uuid4().hex
        async with session_factory() as session, session.begin():
            from hitl_ops.infrastructure.orm import ExecutionORM

            execution_id = (
                await session.execute(
                    select(ExecutionORM.id).where(
                        ExecutionORM.tenant_id == tenant_id,
                        ExecutionORM.intent_id == intent_id,
                        ExecutionORM.intent_revision == revision,
                    )
                )
            ).scalar_one_or_none()
            if execution_id is None:
                continue
            reconciliation = ReconciliationService(session, adapter)
            try:
                await reconciliation.reconcile(
                    execution_id=execution_id, worker_id="execution-worker", command_id=command_id
                )
                stats["reconciled"] += 1
            except DomainError:
                stats["skipped"] += 1
    return stats


def _target_query_for(adapter: InfrastructureAdapter) -> Any:
    """Prefer the adapter's own bounded read path; otherwise fail closed."""

    if callable(getattr(adapter, "fetch", None)):
        return adapter
    return DenyingTargetQuery()
