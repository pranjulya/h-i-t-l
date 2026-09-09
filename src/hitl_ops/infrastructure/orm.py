"""SQLAlchemy ORM mappings for the durable intent aggregate."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from hitl_ops.domain.enums import IntentState


class Base(DeclarativeBase):
    pass


class ActionIntentORM(Base):
    __tablename__ = "action_intents"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_action_intents_revision_positive"),
        CheckConstraint("state_version >= 1", name="ck_action_intents_state_version_positive"),
        Index("ix_action_intents_digest", "intent_digest"),
    )

    tenant_id: Mapped[str] = mapped_column(String(63), primary_key=True)
    intent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool: Mapped[str] = mapped_column(String(63), nullable=False)
    canonical_parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    intent_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    requester_id: Mapped[str] = mapped_column(String(255), nullable=False)
    requester_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    raw_proposal: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=IntentState.REQUESTED.value
    )
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approval_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class StateTransitionORM(Base):
    __tablename__ = "state_transitions"
    __table_args__ = (
        UniqueConstraint("command_id", name="uq_state_transitions_command_id"),
        UniqueConstraint(
            "tenant_id",
            "intent_id",
            "intent_revision",
            "sequence",
            name="uq_state_transitions_revision_sequence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(63), nullable=False)
    intent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    intent_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    command_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IdempotencyRecordORM(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "actor_id",
            "scope",
            "key_hash",
            name="uq_idempotency_records_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(63), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutboxMessageORM(Base):
    __tablename__ = "outbox_messages"
    __table_args__ = (Index("ix_outbox_messages_pending", "published_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditEventORM(Base):
    """Append-only, hash-linked audit evidence (no updates or deletes permitted)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "aggregate_id",
            "sequence",
            name="uq_audit_events_aggregate_sequence",
        ),
        Index("ix_audit_events_aggregate", "tenant_id", "aggregate_id", "sequence"),
        Index(
            "uq_audit_events_causation",
            "tenant_id",
            "aggregate_id",
            "causation_id",
            unique=True,
            postgresql_where=text("causation_id IS NOT NULL"),
        ),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(String(63), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    aggregate_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    risk_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class RiskEvaluationORM(Base):
    __tablename__ = "risk_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "intent_id",
            "intent_revision",
            "generation",
            name="uq_risk_evaluations_revision_generation",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(63), nullable=False)
    intent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    intent_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    band: Mapped[str] = mapped_column(String(16), nullable=False)
    factors: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluated_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PolicyEvaluationORM(Base):
    __tablename__ = "policy_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "intent_id",
            "intent_revision",
            "generation",
            name="uq_policy_evaluations_revision_generation",
        ),
        Index(
            "ix_policy_evaluations_intent",
            "tenant_id",
            "intent_id",
            "intent_revision",
            "generation",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(63), nullable=False)
    intent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    intent_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    route: Mapped[str] = mapped_column(String(32), nullable=False)
    required_roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    required_scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    obligations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    approval_ttl_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PolicyBundleORM(Base):
    __tablename__ = "policy_bundles"
    __table_args__ = (
        Index(
            "uq_policy_bundles_tenant_active",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(63), nullable=False, default="tenant-1")
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApprovalDecisionORM(Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (
        Index(
            "uq_approval_decisions_approving_actor",
            "tenant_id",
            "intent_id",
            "intent_revision",
            "level",
            "actor_id",
            unique=True,
            postgresql_where=text("decision = 'APPROVE'"),
        ),
        CheckConstraint("level IN (1, 2)", name="ck_approval_decisions_level"),
        CheckConstraint("decision IN ('APPROVE', 'REJECT')", name="ck_approval_decisions_decision"),
        CheckConstraint("intent_revision >= 1", name="ck_approval_decisions_revision_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(63), nullable=False)
    intent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    intent_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    intent_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_roles_snapshot: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    scope_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RoleAssignmentORM(Base):
    __tablename__ = "role_assignments"
    __table_args__ = (
        Index(
            "uq_role_assignments_active_grant",
            "tenant_id",
            "principal_id",
            "role",
            "environments_key",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(63), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(63), nullable=False)
    environments: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    environments_key: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    granted_by: Mapped[str] = mapped_column(String(255), nullable=False)


class ExecutionORM(Base):
    __tablename__ = "executions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "intent_id", "intent_revision", name="uq_executions_revision"
        ),
        UniqueConstraint("operation_key", name="uq_executions_operation_key"),
        Index("ix_executions_claim_lease", "status", "claim_expires_at"),
        CheckConstraint("attempt >= 1", name="ck_executions_attempt_positive"),
        CheckConstraint("intent_revision >= 1", name="ck_executions_revision_positive"),
        CheckConstraint(
            "status IN ('CLAIMED', 'EXECUTING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')",
            name="ck_executions_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(63), nullable=False)
    intent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    intent_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_key: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_operation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    precondition_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
