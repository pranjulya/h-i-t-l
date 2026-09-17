"""LLM outage, adapter ambiguity, audit backlog, and Redis-loss tests."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from hitl_ops.adapters.demo import DemoInfrastructureAdapter
from hitl_ops.api.app import create_app
from hitl_ops.infrastructure.notification import RecordingNotificationSink
from tests.api.conftest import api_settings, bearer


def test_model_outage_keeps_direct_api_usable(failure_database) -> None:
    with TestClient(create_app(api_settings())) as client:
        agent_response = client.post(
            "/v1/agent/intents",
            json={"request": "restart api", "context_refs": {}},
            headers={**bearer(), "Idempotency-Key": "llm-1"},
        )
        direct_response = client.post(
            "/v1/intents",
            json={
                "tool": "scale_service",
                "parameters": {"environment": "staging", "service": "api", "replicas": 2},
            },
            headers={**bearer(), "Idempotency-Key": "llm-2"},
        )
    assert agent_response.status_code == 503
    assert agent_response.json()["error"]["code"] == "LLM_UNAVAILABLE"
    assert agent_response.json()["error"]["retryable"] is True
    assert direct_response.status_code == 201


async def test_adapter_timeout_enters_unknown_never_failed() -> None:
    class SlowAdapter(DemoInfrastructureAdapter):
        async def execute(self, tool, command):
            await asyncio.sleep(5)
            return await super().execute(tool, command)

    from hitl_ops.application.execution import ExecutionService, classify_outcome

    service = ExecutionService(None, SlowAdapter())  # type: ignore[arg-type]
    try:
        await asyncio.wait_for(
            service._adapter.execute("scale_service", None),  # type: ignore[arg-type]
            timeout=0.05,
        )
    except TimeoutError:
        outcome, error_code = classify_outcome(None, TimeoutError())
        assert outcome.value == "UNKNOWN"
        assert error_code == "adapter_timeout_after_send"
        return
    raise AssertionError("expected timeout")


async def test_audit_sink_failure_leaves_primary_state_intact(failure_database, engine) -> None:
    """An audit sink outage must not raise out of the publisher or lose the row.

    The row stays unpublished with attempt metadata so the next pass retries it.
    """

    import uuid

    from sqlalchemy import select

    from hitl_ops.infrastructure.audit import AuditWriter
    from hitl_ops.infrastructure.database import build_sessionmaker
    from hitl_ops.infrastructure.orm import OutboxMessageORM
    from hitl_ops.infrastructure.outbox import OutboxPublisher

    class ExplodingWriter(AuditWriter):
        async def append(self, **kwargs: object) -> None:  # type: ignore[override]
            raise RuntimeError("audit sink unavailable")

    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        session.add(
            OutboxMessageORM(
                topic="intent.created",
                payload={
                    "tenant_id": "tenant-1",
                    "intent_id": str(uuid.uuid4()),
                    "revision": 1,
                },
            )
        )

    publisher = OutboxPublisher(
        maker,
        RecordingNotificationSink(),
        audit_writer_factory=ExplodingWriter,
    )
    stats = await publisher.publish_pending()
    assert stats["failed"] >= 1

    async with maker() as session, session.begin():
        row = (await session.execute(select(OutboxMessageORM))).scalars().one()
        assert row.published_at is None
        assert row.attempts == 1
        assert row.next_attempt_at is not None
        assert row.claim_expires_at is None
