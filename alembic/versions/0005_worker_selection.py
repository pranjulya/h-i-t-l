"""execution worker selection

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-08

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_action_intents_state", "action_intents", ["state"])


def downgrade() -> None:
    op.drop_index("ix_action_intents_state", table_name="action_intents")
