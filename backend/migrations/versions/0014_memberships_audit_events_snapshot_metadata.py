"""Add workspace memberships, append-only audit event records, and snapshot metadata."""

from alembic import op
import sqlalchemy as sa

revision = "0014_memberships_audit_events_snapshot_metadata"
down_revision = "0013_visibility_budget_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic creates ``alembic_version.version_num`` as VARCHAR(32) by
    # default.  This revision identifier is longer, so widen the metadata
    # column before Alembic records the new head on PostgreSQL.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "ALTER TABLE alembic_version "
                "ALTER COLUMN version_num TYPE VARCHAR(64)"
            )
        )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_membership_workspace_user"),
        sa.CheckConstraint("role IN ('viewer', 'operator', 'reviewer', 'admin')", name="ck_membership_role"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("target_type", sa.String(120), nullable=False),
        sa.Column("target_id", sa.String(120), nullable=False),
        sa.Column("before_version_json", sa.Text(), nullable=False),
        sa.Column("after_version_json", sa.Text(), nullable=False),
        sa.Column("run_id", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("page_snapshots") as batch:
        batch.add_column(sa.Column("artifact_uri", sa.String(2048), nullable=True))
        batch.add_column(sa.Column("parser_version", sa.String(120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("page_snapshots") as batch:
        batch.drop_column("parser_version")
        batch.drop_column("artifact_uri")
    op.drop_table("audit_events")
    op.drop_table("memberships")
