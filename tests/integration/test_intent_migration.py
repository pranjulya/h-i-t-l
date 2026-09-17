"""Migration rehearsal and PostgreSQL constraint integration tests."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.infrastructure.orm import (
    ActionIntentORM,
    IdempotencyRecordORM,
    OutboxMessageORM,
    StateTransitionORM,
)
from tests.integration.conftest import run_alembic_downgrade, run_alembic_upgrade

_ALL_TABLES = {"action_intents", "state_transitions", "idempotency_records", "outbox_messages"}


def _intent(
    *,
    tenant_id: str = "tenant-1",
    intent_id: uuid.UUID | None = None,
    revision: int = 1,
) -> ActionIntentORM:
    return ActionIntentORM(
        tenant_id=tenant_id,
        intent_id=intent_id if intent_id is not None else uuid.uuid4(),
        revision=revision,
        tool="scale_service",
        canonical_parameters={},
        intent_digest="a" * 64,
        requester_id="user-1",
        requester_rationale="r",
        source="AGENT",
        state="REQUESTED",
        state_version=1,
    )


def _transition(
    *,
    tenant_id: str,
    intent_id: uuid.UUID,
    intent_revision: int = 1,
    command_id: str,
) -> StateTransitionORM:
    return StateTransitionORM(
        tenant_id=tenant_id,
        intent_id=intent_id,
        intent_revision=intent_revision,
        from_state=None,
        to_state="REQUESTED",
        actor_type="system",
        actor_id="user-1",
        command_id=command_id,
        reason_code="intent_created",
        metadata_={},
        sequence=1,
    )


async def _table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        return set(
            await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_table_names()
            )
        )


async def test_upgrade_downgrade_rehearsal(migrated_database: str, engine: AsyncEngine) -> None:
    names = await _table_names(engine)
    assert names >= _ALL_TABLES

    await asyncio.to_thread(run_alembic_downgrade, migrated_database, "base")
    names = await _table_names(engine)
    assert not (_ALL_TABLES & names)

    await asyncio.to_thread(run_alembic_upgrade, migrated_database, "head")
    names = await _table_names(engine)
    assert names >= _ALL_TABLES


async def test_duplicate_revision_is_rejected(migrated_database: str, engine: AsyncEngine) -> None:
    from hitl_ops.infrastructure.database import build_sessionmaker

    maker = build_sessionmaker(engine)
    shared_intent_id = uuid.uuid4()

    def make() -> ActionIntentORM:
        return ActionIntentORM(
            tenant_id="tenant-1",
            intent_id=shared_intent_id,
            revision=1,
            tool="scale_service",
            canonical_parameters={},
            intent_digest="a" * 64,
            requester_id="user-1",
            requester_rationale="r",
            source="AGENT",
            state="REQUESTED",
            state_version=1,
        )

    async with maker() as session, session.begin():
        session.add(make())
    async with maker() as session, session.begin():
        session.add(make())
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_revision_check_constraint_is_enforced(
    migrated_database: str, engine: AsyncEngine
) -> None:
    from hitl_ops.infrastructure.database import build_sessionmaker

    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        session.add(
            ActionIntentORM(
                tenant_id="tenant-1",
                intent_id=uuid.uuid4(),
                revision=0,
                tool="scale_service",
                canonical_parameters={},
                intent_digest="a" * 64,
                requester_id="user-1",
                requester_rationale="r",
                source="AGENT",
                state="REQUESTED",
                state_version=1,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_duplicate_command_id_is_rejected(
    migrated_database: str, engine: AsyncEngine
) -> None:
    from hitl_ops.infrastructure.database import build_sessionmaker

    maker = build_sessionmaker(engine)
    command_id = uuid.uuid4().hex
    intent_id = uuid.uuid4()
    async with maker() as session, session.begin():
        session.add(_intent(tenant_id="tenant-1", intent_id=intent_id))
        session.add(_transition(tenant_id="tenant-1", intent_id=intent_id, command_id=command_id))
    async with maker() as session, session.begin():
        session.add(_intent(tenant_id="tenant-2"))
        session.add(
            _transition(tenant_id="tenant-2", intent_id=uuid.uuid4(), command_id=command_id)
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_orphan_state_transition_is_rejected(
    migrated_database: str, engine: AsyncEngine
) -> None:
    from hitl_ops.infrastructure.database import build_sessionmaker

    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        session.add(
            _transition(tenant_id="tenant-1", intent_id=uuid.uuid4(), command_id=uuid.uuid4().hex)
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_state_transition_foreign_key_constrains_the_intent_revision(
    migrated_database: str, engine: AsyncEngine
) -> None:
    from hitl_ops.infrastructure.database import build_sessionmaker

    maker = build_sessionmaker(engine)
    intent_id = uuid.uuid4()
    async with maker() as session, session.begin():
        session.add(_intent(tenant_id="tenant-1", intent_id=intent_id, revision=1))
        session.add(
            _transition(
                tenant_id="tenant-1",
                intent_id=intent_id,
                intent_revision=2,
                command_id=uuid.uuid4().hex,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_state_transition_foreign_key_exists_in_schema(
    migrated_database: str, engine: AsyncEngine
) -> None:
    async with engine.connect() as connection:
        foreign_keys = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_foreign_keys("state_transitions")
        )
    assert any(
        foreign_key["constrained_columns"] == ["tenant_id", "intent_id", "intent_revision"]
        and foreign_key["referred_table"] == "action_intents"
        and foreign_key["referred_columns"] == ["tenant_id", "intent_id", "revision"]
        for foreign_key in foreign_keys
    )


async def test_idempotency_key_uniqueness_is_enforced(
    migrated_database: str, engine: AsyncEngine
) -> None:
    from hitl_ops.infrastructure.database import build_sessionmaker

    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        session.add(
            IdempotencyRecordORM(
                tenant_id="tenant-1",
                actor_id="user-1",
                scope="intents",
                key_hash="k" * 64,
                request_hash="r" * 64,
                status="COMPLETED",
            )
        )
    async with maker() as session, session.begin():
        session.add(
            IdempotencyRecordORM(
                tenant_id="tenant-1",
                actor_id="user-1",
                scope="intents",
                key_hash="k" * 64,
                request_hash="s" * 64,
                status="COMPLETED",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_outbox_rows_require_payload(migrated_database: str, engine: AsyncEngine) -> None:
    from hitl_ops.infrastructure.database import build_sessionmaker

    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        session.add(OutboxMessageORM(topic="intent.created", payload={"ok": True}))
