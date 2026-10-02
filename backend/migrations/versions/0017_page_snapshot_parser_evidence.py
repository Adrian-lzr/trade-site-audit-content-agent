"""Persist parser identity and independent document evidence hashes."""

from alembic import op
import sqlalchemy as sa


revision = "0017_page_snapshot_parser_evidence"
down_revision = "0016_visibility_answer_evaluation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("page_snapshots") as batch:
        batch.add_column(sa.Column("requested_url", sa.String(2048), nullable=True))
        batch.add_column(sa.Column("final_url", sa.String(2048), nullable=True))
        batch.add_column(sa.Column("declared_canonical_json", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("normalized_canonical_json", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("metadata_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("body_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("page_snapshots") as batch:
        batch.drop_column("body_hash")
        batch.drop_column("metadata_hash")
        batch.drop_column("normalized_canonical_json")
        batch.drop_column("declared_canonical_json")
        batch.drop_column("final_url")
        batch.drop_column("requested_url")
