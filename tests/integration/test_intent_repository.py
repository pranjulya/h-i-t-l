"""Intent repository integration tests: atomic state, transition, and outbox."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from hitl_ops.domain.enums import IntentSource, ToolName
from hitl_ops.domain.models import CreateIntentCommand
from hitl_ops.infrastructure.orm import ActionIntentORM, OutboxMessageORM, StateTransitionORM
from hitl_ops.infrastructure.repositories import IntentRepository


def _command(**overrides: object) -> CreateIntentCommand:
    values: dict[str, object] = {
        "tenant_id": "tenant-1",
        "intent_id": uuid.uuid4(),
        "revision": 1,
        "tool": ToolName.SCALE_SERVICE,
        "canonical_parameters": {"environment": "staging", "replicas": 4, "service": "api"},
        "intent_digest": "a" * 64,
        "requester_id": "user-1",
        "requester_rationale": "scale up for load test",
        "source": IntentSource.AGENT,
        "raw_proposal": {"service": "api", "api_key": "sk-123"},
        "command_id": uuid.uuid4().hex,
        "correlation_id": uuid.uuid4().hex,
        "actor_id": "user-1",
    }
    values.update(overrides)
    return CreateIntentCommand(**values)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    from hitl_ops.infrastructure.database import build_sessionmaker

    maker = build_sessionmaker(engine)
    async with maker() as established:
        yield established


async def test_create_persists_intent_transition_and_outbox(
    migrated_database: str, session: AsyncSession
) -> None:
    command = _command()
    async with session.begin():
        snapshot = await IntentRepository(session).create(command)
    assert snapshot.state.value == "REQUESTED"
    assert snapshot.state_version == 1
    assert snapshot.tool is ToolName.SCALE_SERVICE

    async with session.begin():
        intent = await session.get(ActionIntentORM, (command.tenant_id, command.intent_id, 1))
        assert intent is not None
        assert intent.canonical_parameters == {
            "environment": "staging",
            "replicas": 4,
            "service": "api",
        }
        assert intent.raw_proposal is not None
        assert intent.raw_proposal["api_key"] == "[REDACTED]"
        assert intent.raw_proposal["service"] == "api"
        assert intent.intent_digest == "a" * 64

        transition = (
            await session.execute(
                select(StateTransitionORM).where(StateTransitionORM.intent_id == command.intent_id)
            )
        ).scalar_one()
        assert transition.to_state == "REQUESTED"
        assert transition.from_state is None
        assert transition.sequence == 1
        assert transition.command_id == command.command_id

        outbox = (
            await session.execute(
                select(OutboxMessageORM).where(
                    OutboxMessageORM.payload["intent_id"].astext == str(command.intent_id)
                )
            )
        ).scalar_one()
        assert outbox.topic == "intent.created"
        assert outbox.payload["intent_digest"] == "a" * 64


async def test_rollback_leaves_neither_intent_nor_transition(
    migrated_database: str, session: AsyncSession
) -> None:
    command = _command()
    with pytest.raises(RuntimeError, match="forced failure"):
        async with session.begin():
            await IntentRepository(session).create(command)
            raise RuntimeError("forced failure")

    async with session.begin():
        intent_count = len(
            (
                await session.execute(
                    select(ActionIntentORM).where(ActionIntentORM.intent_id == command.intent_id)
                )
            ).all()
        )
        transition_count = len(
            (
                await session.execute(
                    select(StateTransitionORM).where(
                        StateTransitionORM.intent_id == command.intent_id
                    )
                )
            ).all()
        )
        outbox_count = len((await session.execute(select(OutboxMessageORM))).all())
    assert (intent_count, transition_count, outbox_count) == (0, 0, 0)


async def test_duplicate_command_id_is_rejected(
    migrated_database: str, session: AsyncSession
) -> None:
    from sqlalchemy.exc import IntegrityError

    command_id = uuid.uuid4().hex
    async with session.begin():
        await IntentRepository(session).create(_command(command_id=command_id))

    async with session.begin():
        with pytest.raises(IntegrityError):
            await IntentRepository(session).create(_command(command_id=command_id))
        await session.rollback()


async def test_oversized_raw_proposal_is_truncated(
    migrated_database: str, session: AsyncSession
) -> None:
    command = _command(raw_proposal={"blob": "x" * 40000, "service": "api"})
    async with session.begin():
        await IntentRepository(session).create(command)
        stored = await session.get(ActionIntentORM, (command.tenant_id, command.intent_id, 1))
    assert stored is not None
    assert stored.raw_proposal is not None
    assert stored.raw_proposal["_truncated"] is True
