"""audit events and outbox delivery

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_messages", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "outbox_messages", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "outbox_messages", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_outbox_messages_pending", "outbox_messages", ["published_at", "created_at"])

    op.create_table(
        "audit_events",
        sa.Column("event_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("aggregate_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_revision", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("causation_id", sa.String(length=64), nullable=True),
        sa.Column("policy_version", sa.String(length=32), nullable=True),
        sa.Column("risk_version", sa.String(length=32), nullable=True),
        sa.Column("attributes", pg.JSONB(), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name="pk_audit_events"),
        sa.UniqueConstraint(
            "tenant_id", "aggregate_id", "sequence", name="uq_audit_events_aggregate_sequence"
        ),
    )
    op.create_index(
        "ix_audit_events_aggregate", "audit_events", ["tenant_id", "aggregate_id", "sequence"]
    )
    op.create_index(
        "uq_audit_events_causation",
        "audit_events",
        ["tenant_id", "aggregate_id", "causation_id"],
        unique=True,
        postgresql_where=sa.text("causation_id IS NOT NULL"),
    )

    # Database-level immutability: even the application role cannot rewrite history.
    op.execute(
        sa.text(
            """
            CREATE FUNCTION audit_events_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'audit_events are append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_audit_events_immutable
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_audit_events_immutable ON audit_events"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS audit_events_immutable()"))
    op.drop_index("uq_audit_events_causation", table_name="audit_events")
    op.drop_index("ix_audit_events_aggregate", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_outbox_messages_pending", table_name="outbox_messages")
    op.drop_column("outbox_messages", "published_at")
    op.drop_column("outbox_messages", "next_attempt_at")
    op.drop_column("outbox_messages", "attempts")
