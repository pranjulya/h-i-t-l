"""intent persistence

Revision ID: 0001
Revises:
Create Date: 2026-09-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "action_intents",
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("intent_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("tool", sa.String(length=63), nullable=False),
        sa.Column("canonical_parameters", pg.JSONB(), nullable=False),
        sa.Column("intent_digest", sa.String(length=64), nullable=False),
        sa.Column("requester_id", sa.String(length=255), nullable=False),
        sa.Column("requester_rationale", sa.Text(), nullable=False),
        sa.Column("raw_proposal", pg.JSONB(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("approval_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id", "intent_id", "revision", name="pk_action_intents"),
        sa.CheckConstraint("revision >= 1", name="ck_action_intents_revision_positive"),
    )
    op.create_index("ix_action_intents_digest", "action_intents", ["intent_digest"])

    op.create_table(
        "state_transitions",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("intent_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("intent_revision", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(length=32), nullable=True),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("command_id", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("metadata", pg.JSONB(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_state_transitions"),
        sa.UniqueConstraint("command_id", name="uq_state_transitions_command_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "intent_id",
            "intent_revision",
            "sequence",
            name="uq_state_transitions_revision_sequence",
        ),
    )

    op.create_table(
        "idempotency_records",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", pg.JSONB(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_records"),
        sa.UniqueConstraint(
            "tenant_id", "actor_id", "scope", "key_hash", name="uq_idempotency_records_key"
        ),
    )

    op.create_table(
        "outbox_messages",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_messages"),
    )


def downgrade() -> None:
    op.drop_table("outbox_messages")
    op.drop_table("idempotency_records")
    op.drop_table("state_transitions")
    op.drop_index("ix_action_intents_digest", table_name="action_intents")
    op.drop_table("action_intents")
