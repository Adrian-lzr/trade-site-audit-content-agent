"""Add frozen rule audit inputs and versioned per-snapshot results."""
from alembic import op
import sqlalchemy as sa


revision = "0003_versioned_audit_rules"
down_revision = "0002_worker_leases_synthetic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sites", sa.Column("audit_policy_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("sites", sa.Column("audit_page_limit", sa.Integer(), nullable=False, server_default="20"))
    op.add_column("page_snapshots", sa.Column("audit_rule_version", sa.String(40), nullable=False, server_default="1.0.0"))
    op.add_column("jobs", sa.Column("audit_input_json", sa.Text(), nullable=False, server_default="{}"))
    op.create_table(
        "audit_rule_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("page_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_id", sa.String(100), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(30), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("remediation_hint", sa.Text(), nullable=False),
        sa.UniqueConstraint("snapshot_id", "rule_id", "version", name="uq_snapshot_rule_version"),
    )


def downgrade() -> None:
    op.drop_table("audit_rule_results")
    op.drop_column("jobs", "audit_input_json")
    op.drop_column("page_snapshots", "audit_rule_version")
    op.drop_column("sites", "audit_page_limit")
    op.drop_column("sites", "audit_policy_json")
