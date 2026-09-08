"""Redaction unit tests: nothing secret-shaped survives into evidence."""

from __future__ import annotations

from hitl_ops.domain.intent import sanitize_raw_proposal


def test_secret_shaped_keys_are_redacted_recursively() -> None:
    sanitized = sanitize_raw_proposal(
        {
            "service": "api",
            "api_key": "sk-live-1",
            "config": {"password": "p", "tokens": [{"access_token": "t"}]},
        }
    )
    text = repr(sanitized)
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["config"]["password"] == "[REDACTED]"
    # The whole "tokens" collection is redacted because its key is secret-shaped.
    assert sanitized["config"]["tokens"] == "[REDACTED]"
    assert "sk-live-1" not in text


def test_execution_result_summaries_are_bounded() -> None:
    sanitized = sanitize_raw_proposal({"summary": "y" * 40000, "tool": "restart_service"})
    assert sanitized["_truncated"] is True


def test_notification_content_contains_only_trusted_fields() -> None:
    from hitl_ops.infrastructure.notification import RecordingNotificationSink

    sink = RecordingNotificationSink()

    async def deliver() -> str:
        return await sink.approval_requested(
            tenant_id="tenant-1",
            intent_id="intent-1",
            revision=1,
            to_state="PENDING_APPROVAL_1",
            correlation_id="c",
        )

    import asyncio

    notification_id = asyncio.run(deliver())
    delivered = sink.deliveries[0]
    assert delivered.notification_id == notification_id
    assert "model rationale never" not in delivered.body
    # The body renders the exact intent link, not free-form model text.
    assert delivered.body.startswith("State PENDING_APPROVAL_1")
