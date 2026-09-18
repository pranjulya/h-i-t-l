"""Audit ordering integration tests: sequence, hash chain, tamper detection."""

from __future__ import annotations

from itertools import pairwise

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.infrastructure.audit import verify_aggregate_chain
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.notification import RecordingNotificationSink
from hitl_ops.infrastructure.orm import AuditEventORM, StateTransitionORM
from hitl_ops.infrastructure.outbox import OutboxPublisher
from tests.integration.conftest import create_approved_intent

_RESTART = {"environment": "staging", "service": "payments-api", "strategy": "rolling"}


async def _approved_intent(engine: AsyncEngine) -> dict:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        pending = await create_approved_intent(session, tool="restart_service", parameters=_RESTART)
    # Publication claims and delivers in its own transactions, so it must run
    # after the intent transaction has committed.
    await OutboxPublisher(maker, RecordingNotificationSink()).publish_pending()
    return pending


async def test_audit_sequence_is_monotonic_without_gaps(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    pending = await _approved_intent(engine)
    async with maker() as session, session.begin():
        rows = (
            (
                await session.execute(
                    select(AuditEventORM)
                    .where(AuditEventORM.aggregate_id == pending["intent_id"])
                    .order_by(AuditEventORM.sequence)
                )
            )
            .scalars()
            .all()
        )
    assert rows
    sequences = [r.sequence for r in rows]
    assert sequences == list(range(1, len(sequences) + 1))
    assert rows[0].previous_hash == "0" * 64
    for current, next_row in pairwise(rows):
        assert next_row.previous_hash == current.event_hash

    async with maker() as session, session.begin():
        valid, problem = await verify_aggregate_chain(session, "tenant-1", pending["intent_id"])
    assert valid, problem


async def test_every_transition_has_exactly_one_audit_event(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    pending = await _approved_intent(engine)
    async with maker() as session, session.begin():
        transition_count = (
            await session.execute(
                select(func.count())
                .select_from(StateTransitionORM)
                .where(StateTransitionORM.intent_id == pending["intent_id"])
            )
        ).scalar_one()
        transitioned_events = (
            await session.execute(
                select(func.count())
                .select_from(AuditEventORM)
                .where(
                    AuditEventORM.aggregate_id == pending["intent_id"],
                    AuditEventORM.event_type == "state_transitioned",
                )
            )
        ).scalar_one()
    # The initial REQUESTED transition is audited as intent_created; every
    # subsequent transition is audited as state_transitioned.
    assert transitioned_events == transition_count - 1
    created_events = (
        await session.execute(
            select(func.count())
            .select_from(AuditEventORM)
            .where(
                AuditEventORM.aggregate_id == pending["intent_id"],
                AuditEventORM.event_type == "intent_created",
            )
        )
    ).scalar_one()
    assert created_events == 1


async def test_publication_follows_causal_order_with_equal_timestamps(
    migrated_database: str, engine: AsyncEngine
) -> None:
    """Rows written in one transaction share created_at; audit must still start
    at the aggregate's creation event and follow insertion order."""

    import uuid

    from hitl_ops.domain.enums import IntentSource, ToolName
    from hitl_ops.domain.models import CreateIntentCommand
    from hitl_ops.infrastructure.notification import RecordingNotificationSink
    from hitl_ops.infrastructure.outbox import OutboxPublisher
    from hitl_ops.infrastructure.repositories import IntentRepository

    maker = build_sessionmaker(engine)
    command = CreateIntentCommand(
        tenant_id="tenant-1",
        intent_id=uuid.uuid4(),
        revision=1,
        tool=ToolName.SCALE_SERVICE,
        canonical_parameters={"environment": "staging", "service": "api", "replicas": 1},
        intent_digest="d" * 64,
        requester_id="user-1",
        requester_rationale="order check",
        source=IntentSource.DIRECT,
        command_id=uuid.uuid4().hex,
        correlation_id=uuid.uuid4().hex,
        actor_id="user-1",
    )
    async with maker() as session, session.begin():
        await IntentRepository(session).create(command)

    # The publisher commits its own delivery transactions, so it runs after the
    # state transaction commits (delivery never holds the state row locks).
    stats = await OutboxPublisher(maker, RecordingNotificationSink()).publish_pending()
    assert stats["audited"] >= 1

    async with maker() as session, session.begin():
        rows = (
            (
                await session.execute(
                    select(AuditEventORM)
                    .where(AuditEventORM.aggregate_id == command.intent_id)
                    .order_by(AuditEventORM.sequence)
                )
            )
            .scalars()
            .all()
        )
    assert rows, "audit evidence was not published"
    assert rows[0].event_type == "intent_created"
    assert [row.sequence for row in rows] == list(range(1, len(rows) + 1))
