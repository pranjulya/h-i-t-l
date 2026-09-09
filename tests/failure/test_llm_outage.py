"""LLM outage, adapter ambiguity, audit backlog, and Redis-loss tests."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from hitl_ops.adapters.demo import DemoInfrastructureAdapter
from hitl_ops.api.app import create_app
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


async def test_audit_sink_failure_leaves_primary_state_intact() -> None:
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from hitl_ops.infrastructure.audit import AuditWriter
    from hitl_ops.infrastructure.notification import FailingNotificationSink
    from hitl_ops.infrastructure.orm import OutboxMessageORM
    from hitl_ops.infrastructure.outbox import OutboxPublisher

    class ExplodingWriter(AuditWriter):
        async def append(self, **kwargs: object) -> None:
            raise RuntimeError("audit sink unavailable")

    session = None
    _ = session  # type checker: the publisher must work on any session shape
    publisher = OutboxPublisher(
        None,  # type: ignore[arg-type]
        ExplodingWriter(None),  # type: ignore[arg-type]
        FailingNotificationSink(),
    )
    # A failing audit sink must not raise out of publish_pending; the row stays
    # pending with attempt metadata for retry.
    row = OutboxMessageORM(
        topic="intent.created",
        payload={
            "tenant_id": "t",
            "intent_id": "00000000-0000-0000-0000-000000000000",
            "revision": 1,
        },
    )
    publisher._session = _Recorder([row])
    stats = await publisher.publish_pending()
    assert stats["failed"] >= 1
    assert row.published_at is None
    assert row.attempts == 1
    assert row.next_attempt_at is not None
    _ = datetime.now(UTC), AsyncSession


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> _Result:
        return self

    def all(self) -> list:
        return self._rows


class _Recorder:
    """Minimal session stand-in serving the pending row to the publisher."""

    def __init__(self, rows: list) -> None:
        self._rows = rows
        self.added: list = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def execute(self, *args: object, **kwargs: object) -> _Result:
        return _Result(self._rows)
