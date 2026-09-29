"""Add workspace-scoped change requests, approvals, publication attempts and outbox."""

from alembic import op
import sqlalchemy as sa


revision = "0005_change_management"
down_revision = "0004_workspace_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "change_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_revision_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_change_requests_workspace_site", "change_requests", ["workspace_id", "site_id"])
    op.create_table(
        "change_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("change_request_id", sa.Integer(), sa.ForeignKey("change_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("base_snapshot_id", sa.Integer(), sa.ForeignKey("page_snapshots.id", ondelete="SET NULL"), nullable=True),
        sa.Column("base_content_hash", sa.String(64), nullable=True),
        sa.Column("field_diff_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("fact_versions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("change_request_id", "revision", name="uq_change_revision_number"),
    )
    op.create_index("ix_change_revisions_request", "change_revisions", ["change_request_id", "revision"])
    op.create_table(
        "change_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("change_request_id", sa.Integer(), sa.ForeignKey("change_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_id", sa.Integer(), sa.ForeignKey("change_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        sa.Column("reviewer", sa.String(200), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_change_approvals_request_revision", "change_approvals", ["change_request_id", "revision_id"])
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("aggregate_type", sa.String(100), nullable=False),
        sa.Column("aggregate_id", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_outbox_idempotency_key"),
    )
    op.create_index("ix_outbox_unpublished", "outbox_events", ["published_at", "created_at"])
    op.create_table(
        "publication_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("change_request_id", sa.Integer(), sa.ForeignKey("change_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_id", sa.Integer(), sa.ForeignKey("change_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("publication_attempts")
    op.drop_index("ix_outbox_unpublished", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_change_approvals_request_revision", table_name="change_approvals")
    op.drop_table("change_approvals")
    op.drop_index("ix_change_revisions_request", table_name="change_revisions")
    op.drop_table("change_revisions")
    op.drop_index("ix_change_requests_workspace_site", table_name="change_requests")
    op.drop_table("change_requests")
