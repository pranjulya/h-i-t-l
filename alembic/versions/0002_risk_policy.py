"""risk and policy evaluation persistence

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-08

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from hitl_ops.domain.policy import SEED_POLICY_BUNDLE_RULES, SEED_POLICY_BUNDLE_VERSION

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_evaluations",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("intent_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("intent_revision", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("band", sa.String(length=16), nullable=False),
        sa.Column("factors", pg.JSONB(), nullable=False),
        sa.Column("rule_version", sa.String(length=32), nullable=False),
        sa.Column("evaluated_context", pg.JSONB(), nullable=False),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_risk_evaluations"),
        sa.UniqueConstraint(
            "tenant_id",
            "intent_id",
            "intent_revision",
            "generation",
            name="uq_risk_evaluations_revision_generation",
        ),
    )

    op.create_table(
        "policy_evaluations",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("intent_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("intent_revision", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("route", sa.String(length=32), nullable=False),
        sa.Column("required_roles", pg.JSONB(), nullable=False),
        sa.Column("required_scopes", pg.JSONB(), nullable=False),
        sa.Column("obligations", pg.JSONB(), nullable=False),
        sa.Column("reason_codes", pg.JSONB(), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("approval_ttl_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_policy_evaluations"),
        sa.UniqueConstraint(
            "tenant_id",
            "intent_id",
            "intent_revision",
            "generation",
            name="uq_policy_evaluations_revision_generation",
        ),
    )

    op.create_table(
        "policy_bundles",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=63), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("rules", pg.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_policy_bundles"),
        sa.UniqueConstraint("tenant_id", "version", name="uq_policy_bundles_tenant_version"),
    )
    op.create_index(
        "uq_policy_bundles_tenant_active",
        "policy_bundles",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.get_bind().execute(
        sa.text(
            "INSERT INTO policy_bundles (id, tenant_id, version, rules, is_active) "
            "VALUES (gen_random_uuid(), 'tenant-1', :version, CAST(:rules AS jsonb), true) "
            "ON CONFLICT (tenant_id, version) DO NOTHING"
        ),
        {"version": SEED_POLICY_BUNDLE_VERSION, "rules": json.dumps(SEED_POLICY_BUNDLE_RULES)},
    )


def downgrade() -> None:
    op.drop_table("policy_bundles")
    op.drop_table("policy_evaluations")
    op.drop_table("risk_evaluations")
