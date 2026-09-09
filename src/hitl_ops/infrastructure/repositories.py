"""Repositories: transactional persistence for the intent aggregate."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from hitl_ops.domain.enums import IntentState
from hitl_ops.domain.intent import sanitize_raw_proposal
from hitl_ops.domain.models import CreateIntentCommand, IntentSnapshot
from hitl_ops.infrastructure.orm import ActionIntentORM, OutboxMessageORM, StateTransitionORM


class IntentRepository:
    """Persists the intent aggregate atomically: state, transition, and outbox."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, command: CreateIntentCommand) -> IntentSnapshot:
        now = datetime.now(UTC)
        intent = ActionIntentORM(
            tenant_id=command.tenant_id,
            intent_id=command.intent_id,
            revision=command.revision,
            tool=command.tool.value,
            canonical_parameters=command.canonical_parameters,
            intent_digest=command.intent_digest,
            requester_id=command.requester_id,
            requester_rationale=command.requester_rationale,
            raw_proposal=(
                sanitize_raw_proposal(command.raw_proposal)
                if command.raw_proposal is not None
                else None
            ),
            source=command.source.value,
            state=IntentState.REQUESTED.value,
            state_version=1,
        )
        transition = StateTransitionORM(
            tenant_id=command.tenant_id,
            intent_id=command.intent_id,
            intent_revision=command.revision,
            from_state=None,
            to_state=IntentState.REQUESTED.value,
            actor_type="system",
            actor_id=command.actor_id,
            command_id=command.command_id,
            reason_code="intent_created",
            metadata_={"correlation_id": command.correlation_id},
            sequence=1,
            occurred_at=now,
        )
        outbox = OutboxMessageORM(
            topic="intent.created",
            payload={
                "tenant_id": command.tenant_id,
                "intent_id": str(command.intent_id),
                "revision": command.revision,
                "tool": command.tool.value,
                "intent_digest": command.intent_digest,
                "command_id": command.command_id,
                "correlation_id": command.correlation_id,
            },
        )
        self._session.add_all([intent, transition, outbox])
        await self._session.flush()
        return IntentSnapshot(
            tenant_id=command.tenant_id,
            intent_id=command.intent_id,
            revision=command.revision,
            tool=command.tool,
            intent_digest=command.intent_digest,
            state=IntentState.REQUESTED,
            state_version=1,
            created_at=now,
        )
