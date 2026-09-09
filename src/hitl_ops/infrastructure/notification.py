"""Notification sink: approval requests rendered from trusted server data only.

Content is a server-rendered exact intent summary with a digest link (the
threat model's notification-deception control); model rationale never appears.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from hitl_ops.observability.telemetry import metrics

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    notification_id: str
    subject: str
    body: str


class NotificationSink(Protocol):
    async def approval_requested(
        self,
        *,
        tenant_id: str,
        intent_id: str,
        revision: int,
        to_state: str,
        correlation_id: str,
    ) -> str: ...


class LoggingNotificationSink:
    """Demo sink: renders and logs the notification; delivery id is deterministic."""

    async def approval_requested(
        self,
        *,
        tenant_id: str,
        intent_id: str,
        revision: int,
        to_state: str,
        correlation_id: str,
    ) -> str:
        notification_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"notification:{tenant_id}:{intent_id}:{revision}:{to_state}"
        ).hex
        message = NotificationMessage(
            notification_id=notification_id,
            subject=f"Approval requested for intent {intent_id} (revision {revision})",
            body=(
                f"State {to_state}: an operational action awaits authorization. "
                f"Review the exact digest at /v1/intents/{intent_id} before approving."
            ),
        )
        metrics.increment("notifications_sent", sink="logging")
        logger.info(
            "approval notification %s for intent %s revision %s: %s",
            notification_id,
            intent_id,
            revision,
            message.body,
        )
        return notification_id


class RecordingNotificationSink:
    """Test sink capturing deliveries; deduplicates by deterministic id."""

    def __init__(self) -> None:
        self.deliveries: list[NotificationMessage] = []

    async def approval_requested(
        self,
        *,
        tenant_id: str,
        intent_id: str,
        revision: int,
        to_state: str,
        correlation_id: str,
    ) -> str:
        notification_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"notification:{tenant_id}:{intent_id}:{revision}:{to_state}"
        ).hex
        message = NotificationMessage(
            notification_id=notification_id,
            subject=f"Approval requested for intent {intent_id} (revision {revision})",
            body=(
                f"State {to_state}: an operational action awaits authorization. "
                f"Review the exact digest at /v1/intents/{intent_id} before approving."
            ),
        )
        self.deliveries.append(message)
        metrics.increment("notifications_sent", sink="recording")
        return notification_id


class FailingNotificationSink:
    """Simulates a sink outage; the outbox retries while approvals stay pending."""

    async def approval_requested(self, **kwargs: Any) -> str:
        raise RuntimeError("notification sink unavailable")
