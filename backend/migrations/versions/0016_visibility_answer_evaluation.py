"""Store independent visibility question evaluation evidence."""

from alembic import op
import sqlalchemy as sa


revision = "0016_visibility_answer_evaluation"
down_revision = "0015_fact_workspace_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("visibility_samples") as batch:
        batch.add_column(sa.Column("answered_question", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("visibility_samples") as batch:
        batch.drop_column("answered_question")
