"""approval decisions and role assignments

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_decisions",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("intent_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("intent_revision", sa.Integer(), nullable=False),
        sa.Column("intent_digest", sa.String(length=64), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("actor_roles_snapshot", pg.JSONB(), nullable=False),
        sa.Column("scope_snapshot", pg.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_approval_decisions"),
    )
    op.create_index(
        "uq_approval_decisions_approving_actor",
        "approval_decisions",
        ["tenant_id", "intent_id", "intent_revision", "level", "actor_id"],
        unique=True,
        postgresql_where=sa.text("decision = 'APPROVE'"),
    )

    op.create_table(
        "role_assignments",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("principal_id", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=63), nullable=False),
        sa.Column("environments", pg.JSONB(), nullable=True),
        sa.Column("environments_key", sa.String(length=512), nullable=False, server_default=""),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_by", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_role_assignments"),
    )
    op.create_index(
        "uq_role_assignments_active_grant",
        "role_assignments",
        ["tenant_id", "principal_id", "role", "environments_key"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_approval_decisions_approving_actor", table_name="approval_decisions")
    op.drop_index("uq_role_assignments_active_grant", table_name="role_assignments")
    op.drop_table("role_assignments")
    op.drop_table("approval_decisions")
