"""Persist visibility configuration snapshots and transactional budget reservations."""

from alembic import op
import sqlalchemy as sa


revision = "0013_visibility_budget_snapshots"
down_revision = "0012_visibility_monitoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("visibility_runs") as batch:
        batch.add_column(sa.Column("provider_config_version", sa.String(120), nullable=False, server_default="visibility-provider-v1"))
        batch.add_column(sa.Column("prompt_version", sa.String(120), nullable=False, server_default="visibility-prompt-v1"))
        batch.add_column(sa.Column("pricing_basis_json", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("request_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("reserved_cost_usd", sa.String(30), nullable=False, server_default="0"))

    with op.batch_alter_table("visibility_samples") as batch:
        batch.add_column(sa.Column("request_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("estimated_cost_usd", sa.String(30), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("visibility_samples") as batch:
        batch.drop_column("estimated_cost_usd")
        batch.drop_column("request_id")
    with op.batch_alter_table("visibility_runs") as batch:
        batch.drop_column("reserved_cost_usd")
        batch.drop_column("request_id")
        batch.drop_column("pricing_basis_json")
        batch.drop_column("prompt_version")
        batch.drop_column("provider_config_version")
