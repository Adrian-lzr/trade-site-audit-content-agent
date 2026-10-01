"""Add leases and idempotency metadata for publication dispatch."""

from alembic import op
import sqlalchemy as sa


revision = "0009_publication_outbox_leases"
down_revision = "0008_content_generation_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("outbox_events") as batch_op:
        batch_op.add_column(sa.Column("lease_token", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("last_error", sa.Text(), nullable=True))

    with op.batch_alter_table("publication_attempts") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("target", sa.String(2048), nullable=True))
        batch_op.add_column(sa.Column("branch", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("commit_sha", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("external_id", sa.String(500), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE publication_attempts "
            "SET idempotency_key = 'legacy-publication-attempt:' || id, "
            "updated_at = created_at "
            "WHERE idempotency_key IS NULL OR updated_at IS NULL"
        )
    )
    with op.batch_alter_table("publication_attempts") as batch_op:
        batch_op.alter_column("idempotency_key", existing_type=sa.String(255), nullable=False)
        batch_op.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.create_unique_constraint("uq_publication_attempt_idempotency_key", ["idempotency_key"])


def downgrade() -> None:
    with op.batch_alter_table("publication_attempts") as batch_op:
        batch_op.drop_constraint("uq_publication_attempt_idempotency_key", type_="unique")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("external_id")
        batch_op.drop_column("commit_sha")
        batch_op.drop_column("branch")
        batch_op.drop_column("target")
        batch_op.drop_column("idempotency_key")
    with op.batch_alter_table("outbox_events") as batch_op:
        batch_op.drop_column("last_error")
        batch_op.drop_column("attempts")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("lease_token")
