"""execution worker selection

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_action_intents_state", "action_intents", ["state"])
    op.add_column(
        "executions",
        sa.Column("reconcile_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "executions", sa.Column("next_reconcile_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_executions_reconciliation_due", "executions", ["status", "next_reconcile_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_executions_reconciliation_due", table_name="executions")
    op.drop_column("executions", "next_reconcile_at")
    op.drop_column("executions", "reconcile_attempts")
    op.drop_index("ix_action_intents_state", table_name="action_intents")
