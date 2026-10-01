"""Add guarded local rollback attempt metadata."""

from alembic import op
import sqlalchemy as sa


revision = "0011_rollback_attempts"
down_revision = "0010_deployment_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("publication_attempts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "rollback_of_attempt_id",
                sa.Integer(),
                sa.ForeignKey(
                    "publication_attempts.id",
                    ondelete="SET NULL",
                    name="fk_publication_attempts_rollback_of",
                ),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("expected_current_sha", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("rollback_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("publication_attempts") as batch_op:
        batch_op.drop_column("rollback_reason")
        batch_op.drop_column("expected_current_sha")
        batch_op.drop_column("rollback_of_attempt_id")
