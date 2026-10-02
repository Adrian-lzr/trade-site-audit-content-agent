"""Enforce workspace ownership for Fact parent links."""

from alembic import op
import sqlalchemy as sa

revision = "0015_fact_workspace_integrity"
down_revision = "0014_memberships_audit_events_snapshot_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The composite key makes a parent from another workspace impossible.  The
    # existing single-column FK remains for compatibility with old databases.
    with op.batch_alter_table("facts") as batch:
        batch.create_unique_constraint("uq_fact_workspace_id", ["workspace_id", "id"])
        batch.create_foreign_key(
            "fk_fact_parent_workspace",
            "facts",
            ["workspace_id", "parent_id"],
            ["workspace_id", "id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("facts") as batch:
        batch.drop_constraint("fk_fact_parent_workspace", type_="foreignkey")
        batch.drop_constraint("uq_fact_workspace_id", type_="unique")
