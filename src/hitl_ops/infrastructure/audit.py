"""Hash-linked audit evidence.

Every committed state change yields one audit event. ``event_hash`` binds the
canonical event body to the previous event of the same aggregate, so any edit
or gap is detectable. Payloads carry references and sanitized summaries only.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hitl_ops.infrastructure.orm import AuditEventORM, OutboxMessageORM


def canonical_event_body(
    *,
    tenant_id: str,
    aggregate_id: uuid.UUID,
    aggregate_revision: int,
    sequence: int,
    event_type: str,
    actor_id: str,
    occurred_at: datetime,
    correlation_id: str | None,
    causation_id: str | None,
    policy_version: str | None,
    risk_version: str | None,
    attributes: dict[str, Any],
    previous_hash: str,
) -> str:
    body = {
        "aggregate_id": str(aggregate_id),
        "aggregate_revision": aggregate_revision,
        "actor_id": actor_id,
        "attributes": attributes,
        "causation_id": causation_id,
        "correlation_id": correlation_id,
        "event_type": event_type,
        "occurred_at": occurred_at.isoformat(),
        "policy_version": policy_version,
        "previous_hash": previous_hash,
        "revision": sequence,
        "risk_version": risk_version,
        "sequence": sequence,
        "tenant_id": tenant_id,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_event_hash(**kwargs: Any) -> str:
    body = canonical_event_body(**kwargs)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: uuid.UUID
    tenant_id: str
    aggregate_id: uuid.UUID
    sequence: int
    event_type: str
    event_hash: str
    occurred_at: datetime
    attributes: dict[str, Any]


class AuditWriter:
    """Appends hash-linked events in the same transaction as the publisher."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        tenant_id: str,
        aggregate_id: uuid.UUID,
        aggregate_revision: int,
        event_type: str,
        actor_id: str,
        occurred_at: datetime,
        attributes: dict[str, Any],
        correlation_id: str | None = None,
        causation_id: str | None = None,
        policy_version: str | None = None,
        risk_version: str | None = None,
    ) -> AuditEvent:
        # Serialize appends per aggregate so two publishers cannot allocate the
        # same next sequence or previous hash and then lose the uniqueness race.
        await self._session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtext(tenant_id), func.hashtext(str(aggregate_id))
                )
            )
        )
        if causation_id is not None:
            existing = (
                await self._session.execute(
                    select(AuditEventORM).where(
                        AuditEventORM.tenant_id == tenant_id,
                        AuditEventORM.aggregate_id == aggregate_id,
                        AuditEventORM.causation_id == causation_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return AuditEvent(
                    event_id=existing.event_id,
                    tenant_id=existing.tenant_id,
                    aggregate_id=existing.aggregate_id,
                    sequence=existing.sequence,
                    event_type=existing.event_type,
                    event_hash=existing.event_hash,
                    occurred_at=existing.occurred_at,
                    attributes=existing.attributes,
                )
        last = (
            await self._session.execute(
                select(
                    AuditEventORM.sequence,
                    AuditEventORM.event_hash,
                )
                .where(
                    AuditEventORM.tenant_id == tenant_id,
                    AuditEventORM.aggregate_id == aggregate_id,
                )
                .order_by(AuditEventORM.sequence.desc())
                .limit(1)
            )
        ).first()
        sequence = int(last[0]) + 1 if last else 1
        previous_hash = last[1] if last else "0" * 64
        event_hash = compute_event_hash(
            tenant_id=tenant_id,
            aggregate_id=aggregate_id,
            aggregate_revision=aggregate_revision,
            sequence=sequence,
            event_type=event_type,
            actor_id=actor_id,
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            causation_id=causation_id,
            policy_version=policy_version,
            risk_version=risk_version,
            attributes=attributes,
            previous_hash=previous_hash,
        )
        row = AuditEventORM(
            event_id=uuid.uuid4(),
            tenant_id=tenant_id,
            aggregate_id=aggregate_id,
            aggregate_revision=aggregate_revision,
            sequence=sequence,
            event_type=event_type,
            actor_id=actor_id,
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            causation_id=causation_id,
            policy_version=policy_version,
            risk_version=risk_version,
            attributes=attributes,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )
        self._session.add(row)
        await self._session.flush()
        return AuditEvent(
            event_id=row.event_id,
            tenant_id=tenant_id,
            aggregate_id=aggregate_id,
            sequence=sequence,
            event_type=event_type,
            event_hash=event_hash,
            occurred_at=occurred_at,
            attributes=attributes,
        )

    async def aggregate_count(self, tenant_id: str, aggregate_id: uuid.UUID) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(AuditEventORM)
                .where(
                    AuditEventORM.tenant_id == tenant_id,
                    AuditEventORM.aggregate_id == aggregate_id,
                )
            )
        )

    @staticmethod
    def outbox_row_from_audit(event: AuditEvent) -> OutboxMessageORM:
        return OutboxMessageORM(
            topic=f"audit.{event.event_type}",
            payload={
                "tenant_id": event.tenant_id,
                "aggregate_id": str(event.aggregate_id),
                "sequence": event.sequence,
                "event_hash": event.event_hash,
            },
        )


async def verify_aggregate_chain(
    session: AsyncSession, tenant_id: str, aggregate_id: uuid.UUID
) -> tuple[bool, str | None]:
    """Recompute the hash chain; return (valid, first_problem)."""

    rows = (
        (
            await session.execute(
                select(AuditEventORM)
                .where(
                    AuditEventORM.tenant_id == tenant_id,
                    AuditEventORM.aggregate_id == aggregate_id,
                )
                .order_by(AuditEventORM.sequence)
            )
        )
        .scalars()
        .all()
    )
    previous_hash = "0" * 64
    expected_sequence = 1
    if not rows:
        return False, "empty audit chain"
    for row in rows:
        if row.sequence != expected_sequence:
            return False, f"sequence gap at {row.sequence}"
        recomputed = compute_event_hash(
            tenant_id=row.tenant_id,
            aggregate_id=row.aggregate_id,
            aggregate_revision=row.aggregate_revision,
            sequence=row.sequence,
            event_type=row.event_type,
            actor_id=row.actor_id,
            occurred_at=row.occurred_at,
            correlation_id=row.correlation_id,
            causation_id=row.causation_id,
            policy_version=row.policy_version,
            risk_version=row.risk_version,
            attributes=row.attributes,
            previous_hash=row.previous_hash,
        )
        if row.previous_hash != previous_hash:
            return False, f"broken link at sequence {row.sequence}"
        if row.event_hash != recomputed:
            return False, f"hash mismatch at sequence {row.sequence}"
        previous_hash = row.event_hash
        expected_sequence += 1
    return True, None
