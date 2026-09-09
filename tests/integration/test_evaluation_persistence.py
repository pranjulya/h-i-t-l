"""Evaluation persistence integration tests: evidence, transitions, idempotency."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from hitl_ops.domain.enums import IntentSource, PolicyDisposition, RiskBand, ToolName
from hitl_ops.domain.models import CreateIntentCommand
from hitl_ops.domain.policy import (
    SEED_POLICY_BUNDLE_RULES,
    SEED_POLICY_BUNDLE_VERSION,
    PolicyBundle,
    evaluate_policy,
)
from hitl_ops.domain.risk import RiskContext, evaluate_risk
from hitl_ops.infrastructure.database import build_sessionmaker
from hitl_ops.infrastructure.orm import (
    ActionIntentORM,
    OutboxMessageORM,
    PolicyEvaluationORM,
    RiskEvaluationORM,
    StateTransitionORM,
)
from hitl_ops.infrastructure.repositories import (
    EvaluationRepository,
    IntentRepository,
    PolicyBundleRepository,
)


def _command() -> CreateIntentCommand:
    return CreateIntentCommand(
        tenant_id="tenant-1",
        intent_id=uuid.uuid4(),
        revision=1,
        tool=ToolName.SCALE_SERVICE,
        canonical_parameters={"environment": "staging", "replicas": 4, "service": "api"},
        intent_digest="b" * 64,
        requester_id="user-1",
        requester_rationale="scale up",
        source=IntentSource.AGENT,
        command_id=uuid.uuid4().hex,
        correlation_id=uuid.uuid4().hex,
        actor_id="user-1",
    )


@pytest.fixture
def bundle() -> PolicyBundle:
    return PolicyBundle(version=SEED_POLICY_BUNDLE_VERSION, rules=SEED_POLICY_BUNDLE_RULES)


async def test_record_persists_evidence_and_routes_intent(
    migrated_database: str, engine: AsyncEngine, bundle: PolicyBundle
) -> None:
    maker = build_sessionmaker(engine)
    command = _command()
    async with maker() as session, session.begin():
        await IntentRepository(session).create(command)

    async with maker() as session, session.begin():
        risk = evaluate_risk(command.tool, command.canonical_parameters, RiskContext())
        policy = evaluate_policy(command.tool, risk, command.canonical_parameters, bundle)
        recorded = await EvaluationRepository(session).record(
            command.tenant_id,
            command.intent_id,
            command.revision,
            risk,
            policy,
            command_id=command.command_id,
            correlation_id=command.correlation_id,
        )

    assert recorded.state.value == "AUTO_APPROVED"  # staging scale is allowed
    assert recorded.state_version == 4
    assert recorded.band is RiskBand.MEDIUM
    assert recorded.disposition is PolicyDisposition.ALLOW

    async with maker() as session, session.begin():
        intent = await session.get(ActionIntentORM, (command.tenant_id, command.intent_id, 1))
        assert intent is not None
        assert intent.state == "AUTO_APPROVED"
        assert intent.state_version == 4

        risk_row = (
            await session.execute(
                select(RiskEvaluationORM).where(RiskEvaluationORM.intent_id == command.intent_id)
            )
        ).scalar_one()
        assert risk_row.band == "MEDIUM"
        assert risk_row.rule_version == "risk-rules-1"

        policy_row = (
            await session.execute(
                select(PolicyEvaluationORM).where(
                    PolicyEvaluationORM.intent_id == command.intent_id
                )
            )
        ).scalar_one()
        assert policy_row.disposition == "ALLOW"
        assert policy_row.policy_version == SEED_POLICY_BUNDLE_VERSION

        transitions = (
            (
                await session.execute(
                    select(StateTransitionORM)
                    .where(StateTransitionORM.intent_id == command.intent_id)
                    .order_by(StateTransitionORM.sequence)
                )
            )
            .scalars()
            .all()
        )
        assert [(t.sequence, t.from_state, t.to_state) for t in transitions] == [
            (1, None, "REQUESTED"),
            (2, "REQUESTED", "RISK_EVALUATED"),
            (3, "RISK_EVALUATED", "POLICY_EVALUATED"),
            (4, "POLICY_EVALUATED", "AUTO_APPROVED"),
        ]

        topics = (
            (
                await session.execute(
                    select(OutboxMessageORM.topic).where(
                        OutboxMessageORM.payload["intent_id"].astext == str(command.intent_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(topics) == {"intent.created", "risk.evaluated", "policy.evaluated"}


async def test_record_is_idempotent_per_revision(
    migrated_database: str, engine: AsyncEngine, bundle: PolicyBundle
) -> None:
    maker = build_sessionmaker(engine)
    command = _command()
    async with maker() as session, session.begin():
        await IntentRepository(session).create(command)

    async with maker() as session, session.begin():
        risk = evaluate_risk(command.tool, command.canonical_parameters, RiskContext())
        policy = evaluate_policy(command.tool, risk, command.canonical_parameters, bundle)
        first = await EvaluationRepository(session).record(
            command.tenant_id,
            command.intent_id,
            1,
            risk,
            policy,
            command_id=command.command_id,
            correlation_id=command.correlation_id,
        )
        repeat = await EvaluationRepository(session).record(
            command.tenant_id,
            command.intent_id,
            1,
            risk,
            policy,
            command_id=uuid.uuid4().hex,
            correlation_id=command.correlation_id,
        )

    assert repeat.state == first.state
    async with maker() as session, session.begin():
        risk_count = (
            await session.execute(
                select(func.count())
                .select_from(RiskEvaluationORM)
                .where(RiskEvaluationORM.intent_id == command.intent_id)
            )
        ).scalar_one()
        transition_count = (
            await session.execute(
                select(func.count())
                .select_from(StateTransitionORM)
                .where(StateTransitionORM.intent_id == command.intent_id)
            )
        ).scalar_one()
    assert risk_count == 1
    assert transition_count == 4


async def test_blocked_policy_routes_to_blocked_state(
    migrated_database: str, engine: AsyncEngine
) -> None:
    maker = build_sessionmaker(engine)
    command = _command()
    bundle = PolicyBundle(
        version="policy-test",
        rules={"tools": {"scale_service": {"staging": {"disposition": "BLOCK"}}}},
    )
    async with maker() as session, session.begin():
        await IntentRepository(session).create(command)
    async with maker() as session, session.begin():
        risk = evaluate_risk(command.tool, command.canonical_parameters, RiskContext())
        policy = evaluate_policy(command.tool, risk, command.canonical_parameters, bundle)
        recorded = await EvaluationRepository(session).record(
            command.tenant_id,
            command.intent_id,
            1,
            risk,
            policy,
            command_id=command.command_id,
            correlation_id=command.correlation_id,
        )
    assert recorded.state.value == "BLOCKED"


async def test_active_bundle_loads_from_seed(migrated_database: str, engine: AsyncEngine) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        loaded = await PolicyBundleRepository(session).get_active("tenant-1")
    assert loaded is not None
    assert loaded.version == "policy-1"
    assert "scale_service" in loaded.rules["tools"]


async def test_record_rejects_unknown_intent(
    migrated_database: str, engine: AsyncEngine, bundle: PolicyBundle
) -> None:
    maker = build_sessionmaker(engine)
    async with maker() as session, session.begin():
        risk = evaluate_risk(ToolName.SCALE_SERVICE, {}, RiskContext())
        policy = evaluate_policy(ToolName.SCALE_SERVICE, risk, {}, bundle)
        with pytest.raises(LookupError):
            await EvaluationRepository(session).record(
                "tenant-1",
                uuid.uuid4(),
                1,
                risk,
                policy,
                command_id=uuid.uuid4().hex,
                correlation_id="c",
            )
