"""Add conservative public visibility to workspace facts."""

from alembic import op
import sqlalchemy as sa


revision = "0006_fact_visibility"
down_revision = "0005_change_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("facts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "visibility",
                sa.String(20),
                nullable=False,
                server_default="internal_only",
            )
        )
        batch_op.create_check_constraint(
            "ck_fact_visibility", "visibility IN ('public', 'internal_only')"
        )


def downgrade() -> None:
    with op.batch_alter_table("facts") as batch_op:
        batch_op.drop_constraint("ck_fact_visibility", type_="check")
        batch_op.drop_column("visibility")
