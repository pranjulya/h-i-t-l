"""Idempotency transaction integration tests: replay, conflict, races."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.application.commands import IdempotencyService
from hitl_ops.domain.errors import IdempotencyConflictError
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.orm import IdempotencyRecordORM


def _reserve(engine: AsyncEngine, key: str, body: dict, tenant: str = "tenant-1"):
    maker = build_sessionmaker(engine)

    async def run():
        async with maker() as session, session.begin():
            return await IdempotencyService(session).begin(
                tenant_id=tenant,
                actor_id="user-1",
                scope="intents",
                key=key,
                request_payload=body,
            )

    return run


async def test_same_key_and_body_replays_original_response(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    key = "idem-1"
    body = {"tool": "scale_service", "parameters": {"replicas": 4, "service": "api"}}

    async with maker() as session, session.begin():
        first = await IdempotencyService(session).begin(
            tenant_id="tenant-1",
            actor_id="user-1",
            scope="intents",
            key=key,
            request_payload=body,
        )
    assert first.replayed is False

    async with maker() as session, session.begin():
        await IdempotencyService(session).complete(
            tenant_id="tenant-1",
            actor_id="user-1",
            scope="intents",
            key=key,
            response_status=201,
            response_body={"intent_id": "abc", "state": "REQUESTED"},
        )

    # A semantically identical body in different key order still replays.
    replay_body = {"parameters": {"service": "api", "replicas": 4}, "tool": "scale_service"}
    run = _reserve(engine, key, replay_body)
    reservation = await run()
    assert reservation.replayed is True
    assert reservation.response_status == 201
    assert reservation.response_body == {"intent_id": "abc", "state": "REQUESTED"}


async def test_same_key_changed_body_conflicts(migrated_database: str, engine: AsyncEngine) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await IdempotencyService(session).begin(
            tenant_id="tenant-1",
            actor_id="user-1",
            scope="intents",
            key="idem-2",
            request_payload={"replicas": 4},
        )
    async with maker() as session, session.begin():
        with pytest.raises(IdempotencyConflictError):
            await IdempotencyService(session).begin(
                tenant_id="tenant-1",
                actor_id="user-1",
                scope="intents",
                key="idem-2",
                request_payload={"replicas": 5},
            )


async def test_pending_reservation_is_reported(migrated_database: str, engine: AsyncEngine) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await IdempotencyService(session).begin(
            tenant_id="tenant-1",
            actor_id="user-1",
            scope="intents",
            key="idem-3",
            request_payload={"replicas": 4},
        )
    async with maker() as session, session.begin():
        reservation = await IdempotencyService(session).begin(
            tenant_id="tenant-1",
            actor_id="user-1",
            scope="intents",
            key="idem-3",
            request_payload={"replicas": 4},
        )
    assert reservation.replayed is True
    assert reservation.pending is True


async def test_concurrent_same_key_inserts_have_one_winner(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)

    async def attempt() -> str:
        async with maker() as session, session.begin():
            try:
                await IdempotencyService(session).begin(
                    tenant_id="tenant-1",
                    actor_id="user-1",
                    scope="intents",
                    key="race-key",
                    request_payload={"replicas": 4},
                )
                return "reserved"
            except IntegrityError:
                return "conflicted"

    results = await asyncio.gather(attempt(), attempt())
    assert sorted(results) == ["conflicted", "reserved"]


async def test_scope_and_actor_partition_keys(migrated_database: str, engine: AsyncEngine) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        await IdempotencyService(session).begin(
            tenant_id="tenant-1",
            actor_id="user-1",
            scope="intents",
            key="shared-key",
            request_payload={"a": 1},
        )
    async with maker() as session, session.begin():
        other_actor = await IdempotencyService(session).begin(
            tenant_id="tenant-1",
            actor_id="user-2",
            scope="intents",
            key="shared-key",
            request_payload={"a": 1},
        )
        assert other_actor.replayed is False
    async with maker() as session, session.begin():
        other_scope = await IdempotencyService(session).begin(
            tenant_id="tenant-1",
            actor_id="user-1",
            scope="cancellations",
            key="shared-key",
            request_payload={"a": 1},
        )
        assert other_scope.replayed is False


async def test_uniqueness_constraint_backs_the_service(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        session.add(
            IdempotencyRecordORM(
                tenant_id="tenant-1",
                actor_id="user-1",
                scope="intents",
                key_hash="k" * 64,
                request_hash="r" * 64,
                status="PENDING",
            )
        )
    async with maker() as session, session.begin():
        session.add(
            IdempotencyRecordORM(
                tenant_id="tenant-1",
                actor_id="user-1",
                scope="intents",
                key_hash="k" * 64,
                request_hash="r" * 64,
                status="PENDING",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
