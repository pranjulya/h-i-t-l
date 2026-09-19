"""database-backed authorization scopes

Scopes become current authority stored on role assignments (ADR-008), so an
approver's scopes can be rechecked before execution exactly like their roles.
Existing assignments keep working with no scopes, which denies scope-gated
actions until an operator grants them explicitly.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("role_assignments", sa.Column("scopes", pg.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("role_assignments", "scopes")
