"""Add resumable content-generation task and item records."""

from alembic import op
import sqlalchemy as sa


revision = "0008_content_generation_tasks"
down_revision = "0007_procurement_question_sets"
branch_labels = None
depends_on = None


_TASK_STATES = "'queued', 'running', 'succeeded', 'failed', 'needs_information', 'awaiting_review'"


def upgrade() -> None:
    with op.batch_alter_table("change_revisions") as batch_op:
        batch_op.add_column(sa.Column("generation_id", sa.String(200), nullable=True))
        batch_op.create_unique_constraint("uq_change_revision_generation_id", ["generation_id"])

    op.create_table(
        "content_generation_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column(
            "question_set_version_id",
            sa.Integer(),
            sa.ForeignKey("procurement_question_set_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("generation_source", sa.String(20), nullable=False, server_default="fixture"),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"status IN ({_TASK_STATES})", name="ck_content_task_status"),
        sa.CheckConstraint("generation_source IN ('fixture', 'model_api')", name="ck_content_task_generation_source"),
        sa.CheckConstraint("attempts >= 0", name="ck_content_task_attempts_nonnegative"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "site_id"],
            ["sites.workspace_id", "sites.id"],
            name="fk_content_task_site_workspace",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_content_tasks_status_lease", "content_generation_tasks", ["status", "lease_expires_at"])
    op.create_index("ix_content_tasks_workspace_site", "content_generation_tasks", ["workspace_id", "site_id", "created_at"])

    op.create_table(
        "content_generation_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("content_generation_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("procurement_questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_id", sa.Integer(), sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("change_request_id", sa.Integer(), sa.ForeignKey("change_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("page_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("request_summary", sa.Text(), nullable=False),
        sa.Column("required_fact_ids_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("thread_id", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"status IN ({_TASK_STATES})", name="ck_content_item_status"),
        sa.UniqueConstraint("task_id", "question_id", "page_id", name="uq_content_item_task_question_page"),
        sa.UniqueConstraint("thread_id", name="uq_content_item_thread_id"),
    )
    op.create_index("ix_content_items_task_status", "content_generation_items", ["task_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_content_items_task_status", table_name="content_generation_items")
    op.drop_table("content_generation_items")
    op.drop_index("ix_content_tasks_workspace_site", table_name="content_generation_tasks")
    op.drop_index("ix_content_tasks_status_lease", table_name="content_generation_tasks")
    op.drop_table("content_generation_tasks")
    with op.batch_alter_table("change_revisions") as batch_op:
        batch_op.drop_constraint("uq_change_revision_generation_id", type_="unique")
        batch_op.drop_column("generation_id")
