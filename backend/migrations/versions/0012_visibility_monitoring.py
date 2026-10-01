"""Add persisted visibility monitoring runs and source samples."""

from alembic import op
import sqlalchemy as sa


revision = "0012_visibility_monitoring"
down_revision = "0011_rollback_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "visibility_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("question_set_id", sa.Integer(), sa.ForeignKey("procurement_question_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_set_version_id", sa.Integer(), sa.ForeignKey("procurement_question_set_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("provider_kind", sa.String(40), nullable=False),
        sa.Column("provider_model", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("market", sa.String(120), nullable=False),
        sa.Column("language", sa.String(35), nullable=False),
        sa.Column("capability_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("brand_terms_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("budget_usd", sa.String(30), nullable=False, server_default="0"),
        sa.Column("planned_samples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successful_samples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_samples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.String(30), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "site_id"],
            ["sites.workspace_id", "sites.id"],
            name="fk_visibility_run_site_workspace",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial', 'failed')",
            name="ck_visibility_run_status",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_visibility_run_idempotency_key"),
    )
    op.create_index("ix_visibility_runs_workspace_site", "visibility_runs", ["workspace_id", "site_id", "created_at"])
    op.create_index("ix_visibility_runs_status", "visibility_runs", ["status", "created_at"])

    op.create_table(
        "visibility_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("visibility_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("procurement_questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("brand_query", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("citations_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("mentioned_domains_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("provider_request_id", sa.String(255), nullable=True),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.String(30), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'unavailable')",
            name="ck_visibility_sample_status",
        ),
        sa.UniqueConstraint("run_id", "question_id", name="uq_visibility_run_question"),
    )
    op.create_index("ix_visibility_samples_run_status", "visibility_samples", ["run_id", "status"])
    op.create_index("ix_visibility_samples_question", "visibility_samples", ["question_id"])


def downgrade() -> None:
    op.drop_index("ix_visibility_samples_question", table_name="visibility_samples")
    op.drop_index("ix_visibility_samples_run_status", table_name="visibility_samples")
    op.drop_table("visibility_samples")
    op.drop_index("ix_visibility_runs_status", table_name="visibility_runs")
    op.drop_index("ix_visibility_runs_workspace_site", table_name="visibility_runs")
    op.drop_table("visibility_runs")
