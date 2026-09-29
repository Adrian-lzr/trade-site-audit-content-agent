"""Add worker leases and synthetic source markers."""
from alembic import op
import sqlalchemy as sa

revision = "0002_worker_leases_synthetic"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sites", sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("page_snapshots", sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("jobs", sa.Column("lease_token", sa.String(36), nullable=True))
    op.add_column("jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    # Rows from the pre-lease worker have no owner token, so they cannot be safely resumed in place.
    op.execute(
        "UPDATE jobs SET status = 'queued', started_at = NULL, error = 'requeued during worker lease migration' "
        "WHERE status = 'running'"
    )


def downgrade() -> None:
    op.drop_column("jobs", "lease_expires_at")
    op.drop_column("jobs", "lease_token")
    op.drop_column("page_snapshots", "is_synthetic")
    op.drop_column("sites", "is_synthetic")
