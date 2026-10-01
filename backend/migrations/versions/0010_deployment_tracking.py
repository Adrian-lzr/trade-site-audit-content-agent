"""Track deployment callbacks and local publication verification."""

from alembic import op
import sqlalchemy as sa


revision = "0010_deployment_tracking"
down_revision = "0009_publication_outbox_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publication_attempts") as batch_op:
        batch_op.add_column(sa.Column("deployment_status", sa.String(30), nullable=False, server_default="not_started"))
        batch_op.add_column(sa.Column("deployment_id", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("deployed_commit_sha", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publication_attempts") as batch_op:
        batch_op.drop_column("verified_at")
        batch_op.drop_column("deployed_at")
        batch_op.drop_column("deployed_commit_sha")
        batch_op.drop_column("deployment_id")
        batch_op.drop_column("deployment_status")
