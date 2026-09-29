"""Initial schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("workspaces", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("external_id", sa.String(120), unique=True), sa.Column("name", sa.String(200), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("sites", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False), sa.Column("name", sa.String(200), nullable=False), sa.Column("base_url", sa.String(2048), nullable=False), sa.Column("allowed_paths", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("workspace_id", "name", name="uq_site_workspace_name"))
    op.create_table("pages", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("site_id", sa.Integer(), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False), sa.Column("canonical_url", sa.String(2048), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("site_id", "canonical_url", name="uq_page_site_url"))
    op.create_table("jobs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("site_id", sa.Integer(), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("error", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.create_table("page_snapshots", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("page_id", sa.Integer(), sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=False), sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL")), sa.Column("url", sa.String(2048), nullable=False), sa.Column("status_code", sa.Integer(), nullable=False), sa.Column("title", sa.String(500)), sa.Column("content_hash", sa.String(64), nullable=False), sa.Column("content_type", sa.String(200)), sa.Column("content", sa.Text(), nullable=False), sa.Column("headers_json", sa.Text(), nullable=False), sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("audit_findings", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("page_snapshots.id", ondelete="CASCADE"), nullable=False), sa.Column("code", sa.String(100), nullable=False), sa.Column("severity", sa.String(30), nullable=False), sa.Column("message", sa.Text(), nullable=False), sa.Column("evidence_json", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    op.drop_table("audit_findings")
    op.drop_table("page_snapshots")
    op.drop_table("jobs")
    op.drop_table("pages")
    op.drop_table("sites")
    op.drop_table("workspaces")
