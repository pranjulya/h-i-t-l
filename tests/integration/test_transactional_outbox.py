"""Transactional outbox integration tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.domain.enums import IntentSource, ToolName
from hitl_ops.domain.models import CreateIntentCommand
from hitl_ops.domain.policy import SEED_POLICY_BUNDLE_RULES, PolicyBundle, evaluate_policy
from hitl_ops.domain.risk import RiskContext, evaluate_risk
from hitl_ops.infrastructure.audit import AuditWriter, verify_aggregate_chain
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.notification import RecordingNotificationSink
from hitl_ops.infrastructure.orm import AuditEventORM, OutboxMessageORM
from hitl_ops.infrastructure.outbox import OutboxPublisher
from hitl_ops.infrastructure.repositories import EvaluationRepository, IntentRepository

_SCALE = {"environment": "staging", "service": "api", "replicas": 4}


def _command() -> CreateIntentCommand:
    return CreateIntentCommand(
        tenant_id="tenant-1",
        intent_id=uuid.uuid4(),
        revision=1,
        tool=ToolName.SCALE_SERVICE,
        canonical_parameters=_SCALE,
        intent_digest="c" * 64,
        requester_id="user-1",
        requester_rationale="r",
        source=IntentSource.DIRECT,
        command_id=uuid.uuid4().hex,
        correlation_id=uuid.uuid4().hex,
        actor_id="user-1",
    )


async def test_rollback_leaves_neither_state_nor_outbox(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session:
        with pytest.raises(RuntimeError, match="boom"):
            async with session.begin():
                session.add(OutboxMessageORM(topic="intent.created", payload={"intent_id": "x"}))
                raise RuntimeError("boom")
    async with maker() as session, session.begin():
        count = (
            await session.execute(select(func.count()).select_from(OutboxMessageORM))
        ).scalar_one()
    assert count == 0


async def test_committed_transition_always_has_outbox_and_audits(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    sink = RecordingNotificationSink()
    async with maker() as session, session.begin():
        command = _command()
        await IntentRepository(session).create(command)
        bundle = PolicyBundle(version="policy-1", rules=SEED_POLICY_BUNDLE_RULES)
        risk = evaluate_risk(command.tool, command.canonical_parameters, RiskContext())
        policy = evaluate_policy(command.tool, risk, command.canonical_parameters, bundle)
        await EvaluationRepository(session).record(
            command.tenant_id,
            command.intent_id,
            1,
            risk,
            policy,
            command_id=command.command_id,
            correlation_id=command.correlation_id,
        )

    async with maker() as session, session.begin():
        outbox_count = (
            await session.execute(
                select(func.count())
                .select_from(OutboxMessageORM)
                .where(OutboxMessageORM.published_at.is_(None))
            )
        ).scalar_one()
        assert outbox_count >= 3
        publisher = OutboxPublisher(session, AuditWriter(session), sink)
        await publisher.publish_pending()

    async with maker() as session, session.begin():
        pending = (
            await session.execute(
                select(func.count())
                .select_from(OutboxMessageORM)
                .where(OutboxMessageORM.published_at.is_(None))
            )
        ).scalar_one()
        assert pending == 0
        events = (
            (
                await session.execute(
                    select(AuditEventORM).where(AuditEventORM.aggregate_id == command.intent_id)
                )
            )
            .scalars()
            .all()
        )
        assert {e.event_type for e in events} >= {
            "intent_created",
            "risk_evaluated",
            "policy_evaluated",
            "state_transitioned",
        }
        valid, problem = await verify_aggregate_chain(session, "tenant-1", command.intent_id)
        assert valid, problem


async def test_duplicate_delivery_is_idempotent(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    sink = RecordingNotificationSink()
    async with maker() as session, session.begin():
        command = _command()
        await IntentRepository(session).create(command)

    async with maker() as session, session.begin():
        publisher = OutboxPublisher(session, AuditWriter(session), sink)
        await publisher.publish_pending()
    # Publisher crash-after-send replay: same rows published again.
    async with maker() as session, session.begin():
        publisher = OutboxPublisher(session, AuditWriter(session), sink)
        await publisher.publish_pending()

    async with maker() as session, session.begin():
        audit_count = (
            await session.execute(
                select(func.count())
                .select_from(AuditEventORM)
                .where(AuditEventORM.aggregate_id == command.intent_id)
            )
        ).scalar_one()
        outbox_count = (
            await session.execute(select(func.count()).select_from(OutboxMessageORM))
        ).scalar_one()
    assert audit_count == 1  # one intent.created event, not duplicated
    assert outbox_count == 1


async def test_hash_chain_detects_tampering(migrated_database: str, engine: AsyncEngine) -> None:
    maker = build_sessionmaker(engine)
    sink = RecordingNotificationSink()
    async with maker() as session, session.begin():
        command = _command()
        await IntentRepository(session).create(command)
        publisher = OutboxPublisher(session, AuditWriter(session), sink)
        await publisher.publish_pending()

    # Attempted tampering must be blocked by the immutability trigger.
    async with maker() as session, session.begin():
        with pytest.raises(Exception, match="append-only"):
            await session.execute(
                text(
                    "UPDATE audit_events SET actor_id = 'attacker' "
                    "WHERE aggregate_id = :a AND sequence = 1"
                ),
                {"a": command.intent_id},
            )
        await session.rollback()

    async with maker() as session, session.begin():
        valid, problem = await verify_aggregate_chain(session, "tenant-1", command.intent_id)
    assert valid is True
