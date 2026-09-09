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

import asyncio
import uuid
from datetime import UTC, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, OperationalError

from hitl_ops.adapters.base import DenyingTargetQuery, InfrastructureAdapter
from hitl_ops.application.execution import ExecutionService
from hitl_ops.application.reconciliation import ReconciliationService
from hitl_ops.application.revalidation import ExecutionPermit, RevalidationService
from hitl_ops.domain.enums import IntentState
from hitl_ops.domain.errors import DomainError, StateConflictError
from hitl_ops.domain.state_machine import require_transition
from hitl_ops.infrastructure.audit import AuditWriter
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
_UNKNOWN_STATES = (IntentState.EXECUTION_UNKNOWN.value,)
_TICK_LIMIT = 5
# A crashed worker leaves EXECUTING with an expired lease; recovery moves it to
# EXECUTION_UNKNOWN without resending and reconciles from provider evidence.
RECOVERY_LEASE_SECONDS = 60


async def run_worker_tick(
    session_factory: Any,
    adapter: InfrastructureAdapter,
    notification_sink: Any | None = None,
) -> dict[str, int]:
    """One bounded worker pass: execute eligible intents, reconcile unknowns."""

    stats = {"executed": 0, "reconciled": 0, "skipped": 0}

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
        permit = await _claim(session_factory, adapter, tenant_id, intent_id, revision, command_id)
        if permit is None:
            stats["skipped"] += 1
            continue
        executed = await _execute(session_factory, adapter, permit, command_id)
        if executed:
            stats["executed"] += 1
        else:
            stats["skipped"] += 1

    recovered = await _recover_stale_executing(session_factory, adapter)
    stats["recovered"] = recovered

    for tenant_id, intent_id, revision in pending_unknowns:
        command_id = uuid.uuid4().hex
        async with session_factory() as session, session.begin():
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

    async with session_factory() as session, session.begin():
        publisher = OutboxPublisher(
            session,
            AuditWriter(session),
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
        async with session_factory() as session:
            bundle = await PolicyBundleRepository(session).get_active(tenant_id)
            if bundle is None:
                return None
            revalidation = RevalidationService(session)
            # claim_and_revalidate manages its own transactions: it commits
            # phase 1 (claim), fetches the target unlocked, then opens a fresh
            # transaction for phase 3. No outer transaction may wrap it.
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
    adapter: InfrastructureAdapter,
    lease_seconds: int = RECOVERY_LEASE_SECONDS,
) -> int:
    """Move expired EXECUTING leases to EXECUTION_UNKNOWN without resending.

    Exactly one recovery worker wins each execution: the move requires the
    intent row lock plus an atomic status change from EXECUTING with an
    expired lease. Reconciliation afterwards uses provider evidence only.
    """

    recovered = 0
    async with session_factory() as session:
        candidates = (
            await session.execute(
                select(
                    ExecutionORM.id,
                    ExecutionORM.tenant_id,
                    ExecutionORM.intent_id,
                    ExecutionORM.intent_revision,
                )
                .where(ExecutionORM.status == "EXECUTING")
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
                lease = execution.claim_expires_at or execution.started_at
                if lease is None:
                    continue
                # The EXECUTING lease covers the provider call plus the result
                # commit window; only an expired lease is eligible for recovery.
                if lease.tzinfo is None:
                    lease = lease.replace(tzinfo=UTC)
                if lease + timedelta(seconds=lease_seconds) > db_now:
                    continue
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
                recovered += 1
        except (StateConflictError, DBAPIError, OperationalError):
            continue
    # Reconcile whatever this tick just moved to UNKNOWN: provider evidence
    # decides the final outcome, and missing evidence preserves UNKNOWN.
    try:
        async with asyncio.timeout(10):
            async with session_factory() as session:
                moved = (
                    (
                        await session.execute(
                            select(ExecutionORM.id).where(
                                ExecutionORM.status == "UNKNOWN",
                                ExecutionORM.error_code == "executing_lease_expired",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
    except Exception:
        return recovered
    for execution_id in moved:
        command_id = uuid.uuid4().hex
        try:
            async with session_factory() as session, session.begin():
                reconciliation = ReconciliationService(session, adapter)
                await reconciliation.reconcile(
                    execution_id=execution_id,
                    worker_id="execution-worker",
                    command_id=command_id,
                )
        except DomainError:
            continue
    return recovered


def _target_query_for(adapter: InfrastructureAdapter) -> Any:
    """Prefer the adapter's own bounded read path; otherwise fail closed."""

    if callable(getattr(adapter, "fetch", None)):
        return adapter
    return DenyingTargetQuery()
