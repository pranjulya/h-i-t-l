"""risk/policy execution claims

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "executions",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("intent_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("intent_revision", sa.Integer(), nullable=False),
        sa.Column("operation_key", sa.String(length=255), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_operation_id", sa.String(length=255), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("precondition_snapshot", pg.JSONB(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("result_summary", pg.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_executions"),
        sa.UniqueConstraint(
            "tenant_id", "intent_id", "intent_revision", name="uq_executions_revision"
        ),
        sa.UniqueConstraint("operation_key", name="uq_executions_operation_key"),
    )
    op.create_index("ix_executions_claim_lease", "executions", ["status", "claim_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_executions_claim_lease", table_name="executions")
    op.drop_table("executions")
