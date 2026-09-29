"""Add append-only, workspace-scoped business facts."""

from alembic import op
import sqlalchemy as sa


revision = "0004_workspace_facts"
down_revision = "0003_versioned_audit_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("series_id", sa.String(64), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("facts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("predicate", sa.String(200), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(100), nullable=True),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("source_locator", sa.String(2048), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer", sa.String(200), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "series_id", "version", name="uq_fact_series_version"),
    )
    op.create_index("ix_facts_workspace_status", "facts", ["workspace_id", "status"])
    op.create_index("ix_facts_workspace_subject", "facts", ["workspace_id", "subject"])


def downgrade() -> None:
    op.drop_index("ix_facts_workspace_subject", table_name="facts")
    op.drop_index("ix_facts_workspace_status", table_name="facts")
    op.drop_table("facts")
