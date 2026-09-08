"""Repositories: transactional persistence for the intent aggregate."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hitl_ops.domain.enums import ApprovalRoute, IntentState, PolicyDisposition, RiskBand
from hitl_ops.domain.intent import sanitize_raw_proposal
from hitl_ops.domain.models import CreateIntentCommand, IntentSnapshot
from hitl_ops.domain.policy import PolicyBundle, PolicyEvaluation
from hitl_ops.domain.risk import RiskEvaluation
from hitl_ops.infrastructure.orm import (
    ActionIntentORM,
    OutboxMessageORM,
    PolicyBundleORM,
    PolicyEvaluationORM,
    RiskEvaluationORM,
    StateTransitionORM,
)


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


ROUTE_STATE_BY_DISPOSITION: dict[PolicyDisposition, IntentState] = {
    PolicyDisposition.ALLOW: IntentState.AUTO_APPROVED,
    PolicyDisposition.REQUIRE_APPROVAL: IntentState.PENDING_APPROVAL_1,
    PolicyDisposition.BLOCK: IntentState.BLOCKED,
}


@dataclass(frozen=True, slots=True)
class EvaluationRecorded:
    tenant_id: str
    intent_id: uuid.UUID
    revision: int
    state: IntentState
    state_version: int
    band: RiskBand
    disposition: PolicyDisposition
    route: ApprovalRoute


class EvaluationRepository:
    """Records risk/policy evidence and routes the intent via transitions.

    This uses the minimal transition subset that Phase 03's state machine
    subsumes: REQUESTED → RISK_EVALUATED → POLICY_EVALUATED → route state.
    Repeated evaluation commands are idempotent per intent revision.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        tenant_id: str,
        intent_id: uuid.UUID,
        revision: int,
        risk: RiskEvaluation,
        policy: PolicyEvaluation,
        command_id: str,
        correlation_id: str,
    ) -> EvaluationRecorded:
        existing = (
            await self._session.execute(
                select(RiskEvaluationORM).where(
                    RiskEvaluationORM.tenant_id == tenant_id,
                    RiskEvaluationORM.intent_id == intent_id,
                    RiskEvaluationORM.intent_revision == revision,
                    RiskEvaluationORM.generation == 1,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return await self._current_record(tenant_id, intent_id, revision, existing)

        intent = await self._session.get(
            ActionIntentORM, (tenant_id, intent_id, revision), with_for_update=True
        )
        if intent is None:
            raise LookupError("intent revision not found")
        if intent.state != IntentState.REQUESTED.value:
            raise ValueError(f"intent is not awaiting evaluation: {intent.state}")

        now = datetime.now(UTC)
        risk_row = RiskEvaluationORM(
            tenant_id=tenant_id,
            intent_id=intent_id,
            intent_revision=revision,
            generation=1,
            band=risk.band.value,
            factors=list(risk.factors),
            rule_version=risk.rule_version,
            evaluated_context=risk.evaluated_context,
            evaluated_at=now,
        )
        policy_row = PolicyEvaluationORM(
            tenant_id=tenant_id,
            intent_id=intent_id,
            intent_revision=revision,
            generation=1,
            disposition=policy.disposition.value,
            route=policy.route.value,
            required_roles=list(policy.required_roles),
            required_scopes=list(policy.required_scopes),
            obligations=list(policy.obligations),
            reason_codes=list(policy.reason_codes),
            policy_version=policy.policy_version,
            approval_ttl_seconds=policy.approval_ttl_seconds,
            evaluated_at=now,
        )
        route_state = ROUTE_STATE_BY_DISPOSITION[policy.disposition]

        def transition(
            from_state: str | None, to_state: str, suffix: str, sequence: int
        ) -> StateTransitionORM:
            return StateTransitionORM(
                tenant_id=tenant_id,
                intent_id=intent_id,
                intent_revision=revision,
                from_state=from_state,
                to_state=to_state,
                actor_type="system",
                actor_id="risk-policy-engine",
                command_id=f"{command_id}:{suffix}",
                reason_code="evaluation_recorded",
                metadata_={"correlation_id": correlation_id},
                sequence=sequence,
                occurred_at=now,
            )

        transitions = [
            transition(IntentState.REQUESTED.value, IntentState.RISK_EVALUATED.value, "risk", 2),
            transition(
                IntentState.RISK_EVALUATED.value, IntentState.POLICY_EVALUATED.value, "policy", 3
            ),
            transition(IntentState.POLICY_EVALUATED.value, route_state.value, "route", 4),
        ]
        outbox_rows = [
            OutboxMessageORM(
                topic="risk.evaluated",
                payload={
                    "tenant_id": tenant_id,
                    "intent_id": str(intent_id),
                    "revision": revision,
                    "band": risk.band.value,
                    "factors": list(risk.factors),
                    "rule_version": risk.rule_version,
                    "command_id": f"{command_id}:risk",
                    "correlation_id": correlation_id,
                },
            ),
            OutboxMessageORM(
                topic="policy.evaluated",
                payload={
                    "tenant_id": tenant_id,
                    "intent_id": str(intent_id),
                    "revision": revision,
                    "disposition": policy.disposition.value,
                    "route": policy.route.value,
                    "policy_version": policy.policy_version,
                    "command_id": f"{command_id}:policy",
                    "correlation_id": correlation_id,
                },
            ),
        ]
        self._session.add_all([risk_row, policy_row, *transitions, *outbox_rows])

        intent.state = route_state.value
        intent.state_version = 4
        if policy.approval_ttl_seconds is not None:
            intent.approval_expires_at = now + timedelta(seconds=policy.approval_ttl_seconds)

        await self._session.flush()
        return EvaluationRecorded(
            tenant_id=tenant_id,
            intent_id=intent_id,
            revision=revision,
            state=route_state,
            state_version=4,
            band=risk.band,
            disposition=policy.disposition,
            route=policy.route,
        )

    async def _current_record(
        self,
        tenant_id: str,
        intent_id: uuid.UUID,
        revision: int,
        risk_row: RiskEvaluationORM,
    ) -> EvaluationRecorded:
        intent = await self._session.get(ActionIntentORM, (tenant_id, intent_id, revision))
        if intent is None:
            raise LookupError("intent revision not found")
        policy_row = (
            await self._session.execute(
                select(PolicyEvaluationORM).where(
                    PolicyEvaluationORM.tenant_id == tenant_id,
                    PolicyEvaluationORM.intent_id == intent_id,
                    PolicyEvaluationORM.intent_revision == revision,
                    PolicyEvaluationORM.generation == 1,
                )
            )
        ).scalar_one()
        return EvaluationRecorded(
            tenant_id=tenant_id,
            intent_id=intent_id,
            revision=revision,
            state=IntentState(intent.state),
            state_version=intent.state_version,
            band=RiskBand(risk_row.band),
            disposition=PolicyDisposition(policy_row.disposition),
            route=ApprovalRoute(policy_row.route),
        )


class PolicyBundleRepository:
    """Loads the active versioned policy bundle."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active(self) -> PolicyBundle | None:
        row = (
            await self._session.execute(
                select(PolicyBundleORM).where(PolicyBundleORM.is_active.is_(True))
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return PolicyBundle(version=row.version, rules=row.rules)
