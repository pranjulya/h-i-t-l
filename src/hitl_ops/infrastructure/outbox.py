"""Transactional outbox publisher with retry metadata and idempotent delivery.

Delivery is deliberately split into three phases so that no database lock is
ever held across a network call:

1. *claim*   - lock and mark a bounded batch of due rows with a lease, commit.
2. *deliver* - call external sinks with no transaction open.
3. *finalize* - mark published, or schedule a retry, in a short transaction.

A worker that dies mid-delivery leaves an expired lease, so the row becomes
eligible again. Re-delivery is safe because consumers are idempotent: the
audit store deduplicates on causation_id, and notification ids are derived
deterministically from the intent.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
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
    "execution.reconciliation_exhausted",
    "administration.role_changed",
    "administration.policy_changed",
    "notification.delivered",
}
_NOTIFICATION_TOPICS = {"intent.transitioned"}
_NOTIFICATION_STATES = ("PENDING_APPROVAL_1", "PENDING_APPROVAL_2")


class OutboxPublisher:
    """At-least-once delivery; consumers (audit store, notification sink) must be idempotent."""

    def __init__(
        self,
        session_factory: Any,
        notifier: NotificationSink,
        *,
        batch_limit: int = 100,
        retry_backoff_seconds: int = 5,
        claim_lease_seconds: int = 60,
        audit_writer_factory: Callable[[AsyncSession], AuditWriter] = AuditWriter,
    ) -> None:
        self._session_factory = session_factory
        self._notifier = notifier
        self._batch_limit = batch_limit
        self._retry_backoff_seconds = retry_backoff_seconds
        self._claim_lease_seconds = claim_lease_seconds
        self._audit_writer_factory = audit_writer_factory

    async def publish_pending(self, *, worker_id: str = "outbox-publisher") -> dict[str, int]:
        stats = {"audited": 0, "notified": 0, "failed": 0}
        for message_id in await self._claim(worker_id):
            try:
                delivered = await self._deliver(message_id, worker_id=worker_id)
            except Exception:
                await self._reschedule(message_id, worker_id=worker_id)
                stats["failed"] += 1
                continue
            await self._finalize(message_id, worker_id=worker_id)
            stats["audited"] += delivered["audited"]
            stats["notified"] += delivered["notified"]
        return stats

    async def _claim(self, worker_id: str) -> list[uuid.UUID]:
        """Lock a bounded batch of due rows and mark them leased, then commit.

        The lease is claimed in its own short transaction so the row locks are
        released before any external delivery begins.
        """

        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        select(OutboxMessageORM)
                        .where(
                            OutboxMessageORM.published_at.is_(None),
                            or_(
                                OutboxMessageORM.next_attempt_at.is_(None),
                                OutboxMessageORM.next_attempt_at <= now,
                            ),
                            or_(
                                OutboxMessageORM.claim_expires_at.is_(None),
                                OutboxMessageORM.claim_expires_at <= now,
                            ),
                        )
                        .order_by(OutboxMessageORM.sequence)
                        .limit(self._batch_limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            claim_expires_at = now + timedelta(seconds=self._claim_lease_seconds)
            claimed: list[uuid.UUID] = []
            for row in rows:
                row.attempts = (row.attempts or 0) + 1
                row.claimed_by = worker_id
                row.claim_expires_at = claim_expires_at
                claimed.append(row.id)
            return claimed

    async def _deliver(self, message_id: uuid.UUID, *, worker_id: str) -> dict[str, int]:
        """Perform delivery with no row lock held.

        The notification sink is called outside any transaction; the audit
        append runs in its own transaction because it writes the hash chain.
        """

        async with self._session_factory() as session:
            row = await session.get(OutboxMessageORM, message_id)
            topic, payload = (row.topic, dict(row.payload)) if row is not None else (None, {})

        delivered = {"audited": 0, "notified": 0}
        if topic in _AUDIT_TOPICS:
            async with self._session_factory() as session, session.begin():
                row = await session.get(OutboxMessageORM, message_id)
                if row is not None and row.published_at is None:
                    await self._deliver_audit(session, row)
                    delivered["audited"] = 1
        if topic in _NOTIFICATION_TOPICS and payload.get("to_state") in _NOTIFICATION_STATES:
            notification_id = await self._notifier.approval_requested(
                tenant_id=str(payload.get("tenant_id")),
                intent_id=str(payload.get("intent_id")),
                revision=int(payload.get("revision", 1)),
                to_state=str(payload.get("to_state")),
                correlation_id=str(payload.get("command_id")),
            )
            delivered["notified"] = 1
            async with self._session_factory() as session, session.begin():
                session.add(
                    OutboxMessageORM(
                        topic="notification.delivered",
                        payload={
                            "tenant_id": payload.get("tenant_id"),
                            "intent_id": payload.get("intent_id"),
                            "notification_id": notification_id,
                            "delivered_by": worker_id,
                        },
                    )
                )
        return delivered

    async def _finalize(self, message_id: uuid.UUID, *, worker_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(OutboxMessageORM, message_id)
            if row is None or row.published_at is not None:
                return
            if row.claimed_by != worker_id:
                # Another publisher re-claimed after our lease expired; it owns
                # the row now and will finalize it. Clearing the lease here
                # would let a third publisher deliver the same message again.
                return
            row.published_at = datetime.now(UTC)
            row.claimed_by = None
            row.claim_expires_at = None

    async def _reschedule(self, message_id: uuid.UUID, *, worker_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(OutboxMessageORM, message_id)
            if row is None or row.published_at is not None:
                return
            if row.claimed_by != worker_id:
                return
            row.next_attempt_at = datetime.now(UTC) + timedelta(seconds=self._retry_backoff_seconds)
            row.claimed_by = None
            row.claim_expires_at = None

    async def _deliver_audit(self, session: AsyncSession, row: OutboxMessageORM) -> None:
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
            "notification.delivered": "notification_delivered",
        }.get(
            row.topic,
            row.topic.replace("execution.", "execution_").replace(
                "administration.", "administration_"
            ),
        )
        await self._audit_writer_factory(session).append(
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
