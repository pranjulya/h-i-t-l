"""Transactional outbox publisher with retry metadata and idempotent delivery."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hitl_ops.infrastructure.audit import AuditWriter
from hitl_ops.infrastructure.notification import NotificationSink
from hitl_ops.infrastructure.orm import OutboxMessageORM

_AUDIT_TOPICS = {
    "intent.created",
    "intent.transitioned",
    "risk.evaluated",
    "policy.evaluated",
    "execution.succeeded",
    "execution.failed",
    "execution.executing",
    "execution.execution_unknown",
    "execution.reconciled",
    "administration.role_changed",
    "administration.policy_changed",
}
_NOTIFICATION_TOPICS = {"intent.transitioned"}


class OutboxPublisher:
    """At-least-once delivery; consumers (audit store, notification sink) must be idempotent."""

    def __init__(
        self,
        session: AsyncSession,
        audit_writer: AuditWriter,
        notifier: NotificationSink,
        *,
        batch_limit: int = 100,
        retry_backoff_seconds: int = 5,
    ) -> None:
        self._session = session
        self._audit = audit_writer
        self._notifier = notifier
        self._batch_limit = batch_limit
        self._retry_backoff_seconds = retry_backoff_seconds

    async def publish_pending(self, *, worker_id: str = "outbox-publisher") -> dict[str, int]:
        now = datetime.now(UTC)
        rows = (
            (
                await self._session.execute(
                    select(OutboxMessageORM)
                    .where(
                        OutboxMessageORM.published_at.is_(None),
                        (OutboxMessageORM.next_attempt_at.is_(None))
                        | (OutboxMessageORM.next_attempt_at <= now),
                    )
                    .order_by(OutboxMessageORM.created_at)
                    .limit(self._batch_limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        stats = {"audited": 0, "notified": 0, "failed": 0}
        for row in rows:
            row.attempts += 1
            try:
                if row.topic in _AUDIT_TOPICS:
                    await self._deliver_audit(row)
                    stats["audited"] += 1
                if row.topic in _NOTIFICATION_TOPICS and row.payload.get("to_state") in (
                    "PENDING_APPROVAL_1",
                    "PENDING_APPROVAL_2",
                ):
                    await self._deliver_notification(row, worker_id=worker_id)
                    stats["notified"] += 1
                row.published_at = now
            except Exception:
                row.next_attempt_at = now + timedelta(seconds=self._retry_backoff_seconds)
                stats["failed"] += 1
        await self._session.flush()
        return stats

    async def _deliver_audit(self, row: OutboxMessageORM) -> None:
        payload: dict[str, Any] = row.payload
        aggregate_id = (
            uuid.UUID(payload["intent_id"])
            if "intent_id" in payload
            else uuid.uuid5(uuid.NAMESPACE_URL, f"administration:{payload.get('tenant_id')}")
        )
        event_type = {
            "intent.created": "intent_created",
            "intent.transitioned": "state_transitioned",
            "risk.evaluated": "risk_evaluated",
            "policy.evaluated": "policy_evaluated",
        }.get(
            row.topic,
            row.topic.replace("execution.", "execution_").replace(
                "administration.", "administration_"
            ),
        )
        await self._audit.append(
            tenant_id=payload.get("tenant_id", "tenant-1"),
            aggregate_id=aggregate_id,
            aggregate_revision=int(payload.get("revision", 1)),
            event_type=event_type,
            actor_id=str(payload.get("actor_id", payload.get("command_id", "system"))),
            occurred_at=row.created_at,
            attributes=payload,
            correlation_id=payload.get("correlation_id"),
            causation_id=str(row.id),
            policy_version=payload.get("policy_version"),
            risk_version=payload.get("rule_version"),
        )

    async def _deliver_notification(self, row: OutboxMessageORM, *, worker_id: str) -> None:
        payload = row.payload
        message = await self._notifier.approval_requested(
            tenant_id=str(payload.get("tenant_id")),
            intent_id=str(payload.get("intent_id")),
            revision=int(payload.get("revision", 1)),
            to_state=str(payload.get("to_state")),
            correlation_id=str(payload.get("command_id")),
        )
        self._session.add(
            OutboxMessageORM(
                topic="notification.delivered",
                payload={
                    "tenant_id": payload.get("tenant_id"),
                    "intent_id": payload.get("intent_id"),
                    "notification_id": message,
                    "delivered_by": worker_id,
                },
            )
        )
