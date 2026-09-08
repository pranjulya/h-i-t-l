"""Notification delivery integration tests: outbox-driven, retrying, idempotent."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.infrastructure.audit import AuditWriter
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.notification import FailingNotificationSink, RecordingNotificationSink
from hitl_ops.infrastructure.orm import OutboxMessageORM
from hitl_ops.infrastructure.outbox import OutboxPublisher
from tests.integration.conftest import create_pending_intent

_RESTART = {"environment": "staging", "service": "payments-api", "strategy": "rolling"}


async def _pending_with_outbox(engine: AsyncEngine) -> dict:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_pending_intent(session, tool="restart_service", parameters=_RESTART)
    return pending


async def test_approval_request_notification_is_delivered(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    sink = RecordingNotificationSink()
    pending = await _pending_with_outbox(engine)

    async with maker() as session, session.begin():
        publisher = OutboxPublisher(session, AuditWriter(session), sink)
        await publisher.publish_pending()

    approval_notifications = [m for m in sink.deliveries if "PENDING_APPROVAL" in m.body]
    assert len(approval_notifications) == 1
    assert str(pending["intent_id"]) in approval_notifications[0].body


async def test_notification_outage_retries_without_corrupting_state(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    pending = await _pending_with_outbox(engine)

    async with maker() as session, session.begin():
        publisher = OutboxPublisher(session, AuditWriter(session), FailingNotificationSink())
        await publisher.publish_pending()

    # All approval-request outbox rows remain unpublished with attempt metadata.
    async with maker() as session, session.begin():
        pending_rows = (
            (
                await session.execute(
                    select(OutboxMessageORM).where(OutboxMessageORM.published_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        approval_requests = [
            r for r in pending_rows if r.payload.get("to_state") == "PENDING_APPROVAL_1"
        ]
        assert len(approval_requests) == 1
        assert approval_requests[0].attempts >= 1
        assert approval_requests[0].next_attempt_at is not None

        # The approval itself is unaffected by the notification outage.
        intent_state = await session.execute(
            select(OutboxMessageORM.topic).where(
                OutboxMessageORM.payload["intent_id"].astext == str(pending["intent_id"])
            )
        )
        topics = intent_state.scalars().all()
        assert "intent.transitioned" in topics


async def test_duplicate_notification_delivery_is_idempotent(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    sink = RecordingNotificationSink()
    pending = await _pending_with_outbox(engine)

    async with maker() as session, session.begin():
        await OutboxPublisher(session, AuditWriter(session), sink).publish_pending()
    # Simulated redelivery of the same events.
    async with maker() as session, session.begin():
        rows = (
            (
                await session.execute(
                    select(OutboxMessageORM).where(OutboxMessageORM.published_at.is_not(None))
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.published_at = None
        await OutboxPublisher(session, AuditWriter(session), sink).publish_pending()

    # Redelivery produces duplicate messages whose deterministic ids match:
    # the idempotent consumer recognizes them as one logical notification.
    approval_notifications = [m for m in sink.deliveries if "PENDING_APPROVAL" in m.body]
    assert len(approval_notifications) == 2
    assert len({m.notification_id for m in approval_notifications}) == 1
    _ = pending
