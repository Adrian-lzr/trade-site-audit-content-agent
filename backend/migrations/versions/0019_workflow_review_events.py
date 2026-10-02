"""Persist idempotent human review commands for LangGraph recovery."""

from alembic import op
import sqlalchemy as sa


revision = "0019_workflow_review_events"
down_revision = "0018_model_call_budget_accounting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_review_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("change_request_id", sa.Integer(), sa.ForeignKey("change_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_id", sa.Integer(), sa.ForeignKey("change_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        sa.Column("thread_id", sa.String(200), nullable=False),
        sa.Column("decision_id", sa.String(255), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.UniqueConstraint("decision_id", name="uq_workflow_review_decision_id"),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name="ck_workflow_review_decision"),
    )


def downgrade() -> None:
    op.drop_table("workflow_review_events")
