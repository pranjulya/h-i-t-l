"""Notification publisher unit tests: trusted content, idempotent delivery."""

from __future__ import annotations

from hitl_ops.infrastructure.notification import (
    FailingNotificationSink,
    LoggingNotificationSink,
    RecordingNotificationSink,
)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


async def test_recording_sink_delivers_server_rendered_content() -> None:
    sink = RecordingNotificationSink()
    notification_id = await sink.approval_requested(
        tenant_id="tenant-1",
        intent_id="intent-1",
        revision=1,
        to_state="PENDING_APPROVAL_1",
        correlation_id="corr-1",
    )
    assert len(sink.deliveries) == 1
    message = sink.deliveries[0]
    assert message.notification_id == notification_id
    assert "intent-1" in message.subject
    assert "PENDING_APPROVAL_1" in message.body
    assert "/v1/intents/intent-1" in message.body


async def test_delivery_ids_are_deterministic_per_request() -> None:
    sink = RecordingNotificationSink()
    first = await sink.approval_requested(
        tenant_id="tenant-1",
        intent_id="intent-1",
        revision=1,
        to_state="PENDING_APPROVAL_1",
        correlation_id="c",
    )
    second = await RecordingNotificationSink().approval_requested(
        tenant_id="tenant-1",
        intent_id="intent-1",
        revision=1,
        to_state="PENDING_APPROVAL_1",
        correlation_id="different",
    )
    assert first == second


async def test_logging_sink_logs_rendered_notification() -> None:
    from unittest.mock import patch

    sink = LoggingNotificationSink()
    with patch("hitl_ops.infrastructure.notification.logger") as mock_logger:
        notification_id = await sink.approval_requested(
            tenant_id="tenant-1",
            intent_id="intent-1",
            revision=1,
            to_state="PENDING_APPROVAL_1",
            correlation_id="corr-1",
        )
    assert notification_id
    mock_logger.info.assert_called_once()
    logged = mock_logger.info.call_args[0]
    assert "intent-1" in logged[-1] or "intent-1" in str(logged)
    assert "PENDING_APPROVAL_1" in str(logged)


async def test_failing_sink_signals_outage_for_retry() -> None:
    sink = FailingNotificationSink()
    try:
        await sink.approval_requested(
            tenant_id="t",
            intent_id="i",
            revision=1,
            to_state="PENDING_APPROVAL_1",
            correlation_id="c",
        )
    except RuntimeError:
        return
    raise AssertionError("expected outage")
