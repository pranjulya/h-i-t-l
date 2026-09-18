"""outbox publication order

Adds a monotonic insertion sequence to outbox_messages so publication (and the
audit sequence it produces) follows causal insertion order even when several
rows share the same ``created_at`` inside one transaction.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows are numbered in their current created_at order.
    op.execute(
        sa.text(
            "CREATE SEQUENCE outbox_messages_sequence_seq AS bigint START 1 "
            "OWNED BY outbox_messages.id"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE outbox_messages ADD COLUMN sequence bigint NOT NULL "
            "DEFAULT nextval('outbox_messages_sequence_seq')"
        )
    )
    op.execute(
        sa.text(
            "WITH ordered AS ("
            "  SELECT id, row_number() OVER (ORDER BY created_at, id) AS rn"
            "  FROM outbox_messages"
            ") "
            "UPDATE outbox_messages o SET sequence = ordered.rn "
            "FROM ordered WHERE o.id = ordered.id"
        )
    )
    op.execute(
        sa.text(
            "SELECT setval('outbox_messages_sequence_seq', "
            "COALESCE((SELECT MAX(sequence) FROM outbox_messages), 1))"
        )
    )
    op.create_index("ix_outbox_messages_publication_order", "outbox_messages", ["sequence"])


def downgrade() -> None:
    op.drop_index("ix_outbox_messages_publication_order", table_name="outbox_messages")
    op.drop_column("outbox_messages", "sequence")
    op.execute(sa.text("DROP SEQUENCE IF EXISTS outbox_messages_sequence_seq"))
