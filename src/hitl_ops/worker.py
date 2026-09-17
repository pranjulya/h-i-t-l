"""Worker entry point: claim eligible revisions, execute, reconcile.

Worker identity and network access are separate from API/agent components.
The tick is bounded; a scheduler (compose command or CI job) drives it.

The durable claim/``EXECUTING`` transition commits in its own transaction
*before* the provider call. A provider side effect followed by a worker crash
therefore leaves a durable ``EXECUTING`` claim that recovery moves to
``EXECUTION_UNKNOWN`` (never a blind resend) and reconciles from provider
evidence, instead of rolling the claim back and re-sending the operation.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import DBAPIError, OperationalError

from hitl_ops.adapters.base import DenyingTargetQuery, InfrastructureAdapter
from hitl_ops.application.execution import ExecutionService
from hitl_ops.application.reconciliation import (
    MAX_RECONCILE_ATTEMPTS,
    ReconciliationService,
)
from hitl_ops.application.revalidation import ExecutionPermit, RevalidationService
from hitl_ops.domain.enums import IntentState
from hitl_ops.domain.errors import DomainError, StateConflictError
from hitl_ops.domain.state_machine import require_transition
from hitl_ops.infrastructure.notification import LoggingNotificationSink
from hitl_ops.infrastructure.orm import (
    ActionIntentORM,
    ExecutionORM,
    OutboxMessageORM,
    StateTransitionORM,
)
from hitl_ops.infrastructure.outbox import OutboxPublisher
from hitl_ops.infrastructure.repositories import PolicyBundleRepository

_EXECUTABLE_STATES = (IntentState.AUTO_APPROVED.value, IntentState.APPROVED.value)
_TICK_LIMIT = 5
# A crashed worker leaves EXECUTING with an expired lease; recovery moves it to
# EXECUTION_UNKNOWN without resending and reconciles from provider evidence.
RECOVERY_LEASE_SECONDS = 60


async def run_worker_tick(
    session_factory: Any,
    adapter: InfrastructureAdapter,
    notification_sink: Any | None = None,
) -> dict[str, int]:
    """One bounded worker pass: execute eligible intents, reconcile unknowns.

    Reconciliation has exactly one scheduler: this pass. The recovery path may
    move an execution to UNKNOWN and schedule it, but never reconciles on its
    own, so an unresolved provider sees at most one status call per due window.
    """

    stats = {"executed": 0, "reconciled": 0, "skipped": 0}

    # Recovery runs before the snapshot so executions it moves to UNKNOWN are
    # reconciled by the single scheduler below, within this same pass.
    recovered = await _recover_stale_executing(session_factory)
    stats["recovered"] = recovered

    # Snapshot due unknowns before executing: reconciliation is a separate pass
    # and must not immediately resolve what this same tick just timed out.
    async with session_factory() as session:
        pending_unknowns = (
            (
                await session.execute(
                    select(ExecutionORM.id)
                    .where(
                        ExecutionORM.status == "UNKNOWN",
                        ExecutionORM.reconcile_attempts < MAX_RECONCILE_ATTEMPTS,
                        or_(
                            ExecutionORM.next_reconcile_at.is_(None),
                            ExecutionORM.next_reconcile_at <= func.now(),
                        ),
                    )
                    .order_by(ExecutionORM.next_reconcile_at.asc().nulls_first())
                    .limit(_TICK_LIMIT)
                )
            )
            .scalars()
            .all()
        )

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
        permit = await _claim(session_factory, adapter, tenant_id, intent_id, revision, command_id)
        if permit is None:
            stats["skipped"] += 1
            continue
        executed = await _execute(session_factory, adapter, permit, command_id)
        if executed:
            stats["executed"] += 1
        else:
            stats["skipped"] += 1

    for execution_id in pending_unknowns:
        command_id = uuid.uuid4().hex
        async with session_factory() as session, session.begin():
            reconciliation = ReconciliationService(session, adapter)
            try:
                await reconciliation.reconcile(
                    execution_id=execution_id, worker_id="execution-worker", command_id=command_id
                )
                stats["reconciled"] += 1
            except DomainError:
                stats["skipped"] += 1

    publisher = OutboxPublisher(
        session_factory,
        notification_sink or LoggingNotificationSink(),
    )
    publication = await publisher.publish_pending()
    stats["audited"] = publication["audited"]
    stats["notified"] = publication["notified"]
    return stats


async def _claim(
    session_factory: Any,
    adapter: InfrastructureAdapter,
    tenant_id: str,
    intent_id: Any,
    revision: int,
    command_id: str,
) -> ExecutionPermit | None:
    """Claim and revalidate, committing the EXECUTING state durably."""

    try:
        async with session_factory() as session, session.begin():
            bundle = await PolicyBundleRepository(session).get_active(tenant_id)
            if bundle is None:
                return None
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
            if not isinstance(permit, ExecutionPermit):
                return None
            return permit
    except StateConflictError:
        return None


async def _execute(
    session_factory: Any,
    adapter: InfrastructureAdapter,
    permit: ExecutionPermit,
    command_id: str,
) -> bool:
    """Invoke the provider and persist the outcome in a fresh transaction."""

    try:
        async with session_factory() as session, session.begin():
            executor = ExecutionService(session, adapter)
            await executor.execute_permit(
                permit, worker_id="execution-worker", command_id=command_id
            )
        return True
    except StateConflictError:
        return False


async def _recover_stale_executing(
    session_factory: Any,
    lease_seconds: int = RECOVERY_LEASE_SECONDS,
) -> int:
    """Move expired EXECUTING leases to EXECUTION_UNKNOWN without resending.

    Exactly one recovery worker wins each execution: the move requires the
    intent row lock plus an atomic status change from EXECUTING with an
    expired lease. Reconciliation is not performed here: this path only
    schedules the execution, and the single scheduler in run_worker_tick
    reconciles it from provider evidence, so an unresolved provider is not
    polled twice per tick.
    """

    recovered = 0
    lease_expiry = func.coalesce(
        ExecutionORM.claim_expires_at, ExecutionORM.started_at
    ) + func.make_interval(0, 0, 0, 0, 0, 0, lease_seconds)
    async with session_factory() as session:
        candidates = (
            await session.execute(
                select(
                    ExecutionORM.id,
                    ExecutionORM.tenant_id,
                    ExecutionORM.intent_id,
                    ExecutionORM.intent_revision,
                )
                .where(
                    ExecutionORM.status == "EXECUTING",
                    lease_expiry <= func.now(),
                )
                .limit(_TICK_LIMIT)
            )
        ).all()
    for execution_id, tenant_id, intent_id, revision in candidates:
        command_id = uuid.uuid4().hex
        try:
            async with session_factory() as session, session.begin():
                execution = await session.get(ExecutionORM, execution_id)
                if execution is None or execution.status != "EXECUTING":
                    continue
                db_now = (await session.execute(select(func.now()))).scalar_one()
                try:
                    row = (
                        await session.execute(
                            select(ActionIntentORM)
                            .where(
                                ActionIntentORM.tenant_id == tenant_id,
                                ActionIntentORM.intent_id == intent_id,
                                ActionIntentORM.revision == revision,
                            )
                            .with_for_update(nowait=True)
                        )
                    ).scalar_one_or_none()
                except (OperationalError, DBAPIError):
                    continue
                if row is None or IntentState(row.state) is not IntentState.EXECUTING:
                    continue
                require_transition(IntentState.EXECUTING, IntentState.EXECUTION_UNKNOWN)
                sequence = (
                    await session.execute(
                        select(func.max(StateTransitionORM.sequence)).where(
                            StateTransitionORM.tenant_id == tenant_id,
                            StateTransitionORM.intent_id == intent_id,
                            StateTransitionORM.intent_revision == revision,
                        )
                    )
                ).scalar_one()
                next_sequence = int(sequence or 0) + 1
                session.add(
                    StateTransitionORM(
                        tenant_id=tenant_id,
                        intent_id=intent_id,
                        intent_revision=revision,
                        from_state=row.state,
                        to_state=IntentState.EXECUTION_UNKNOWN.value,
                        actor_type="system",
                        actor_id="execution-worker",
                        command_id=f"{command_id}:execution_unknown:{next_sequence}",
                        reason_code="executing_lease_expired",
                        metadata_={},
                        sequence=next_sequence,
                        occurred_at=db_now,
                    )
                )
                session.add(
                    OutboxMessageORM(
                        topic="execution.execution_unknown",
                        payload={
                            "tenant_id": tenant_id,
                            "intent_id": str(intent_id),
                            "revision": revision,
                            "to_state": IntentState.EXECUTION_UNKNOWN.value,
                            "command_id": command_id,
                        },
                    )
                )
                row.state = IntentState.EXECUTION_UNKNOWN.value
                row.state_version += 1
                execution.status = "UNKNOWN"
                execution.error_code = "executing_lease_expired"
                # Hand the execution to the single reconciliation scheduler.
                execution.next_reconcile_at = db_now
                recovered += 1
        except (StateConflictError, DBAPIError, OperationalError):
            continue
    return recovered


def _target_query_for(adapter: InfrastructureAdapter) -> Any:
    """Prefer the adapter's own bounded read path; otherwise fail closed."""

    if callable(getattr(adapter, "fetch", None)):
        return adapter
    return DenyingTargetQuery()
