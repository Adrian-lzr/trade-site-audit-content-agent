"""Add versioned procurement question sets and explicit page mappings."""

from alembic import op
import sqlalchemy as sa


revision = "0007_procurement_question_sets"
down_revision = "0006_fact_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sites") as batch_op:
        batch_op.create_unique_constraint("uq_site_workspace_id", ["workspace_id", "id"])

    op.create_table(
        "procurement_question_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "site_id"],
            ["sites.workspace_id", "sites.id"],
            name="fk_question_set_site_workspace",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "site_id", "name", name="uq_question_set_site_name"),
        sa.UniqueConstraint("id", "site_id", name="uq_question_set_id_site"),
    )
    op.create_index("ix_question_set_workspace_site", "procurement_question_sets", ["workspace_id", "site_id"])

    op.create_table(
        "procurement_question_set_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question_set_id", sa.Integer(), sa.ForeignKey("procurement_question_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("edit_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("state", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_question_set_version_positive"),
        sa.CheckConstraint("edit_version >= 1", name="ck_question_set_edit_version_positive"),
        sa.CheckConstraint("state IN ('draft', 'frozen')", name="ck_question_set_version_state"),
        sa.UniqueConstraint("question_set_id", "version", name="uq_question_set_version_number"),
    )

    op.create_table(
        "procurement_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question_set_version_id", sa.Integer(), sa.ForeignKey("procurement_question_set_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("product", sa.String(200), nullable=False),
        sa.Column("use_case", sa.String(300), nullable=False),
        sa.Column("buyer_role", sa.String(120), nullable=False),
        sa.Column("purchase_stage", sa.String(120), nullable=False),
        sa.Column("target_market", sa.String(120), nullable=False),
        sa.Column("language", sa.String(35), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position BETWEEN 1 AND 20", name="ck_question_position_range"),
        sa.UniqueConstraint("question_set_version_id", "position", name="uq_question_version_position"),
    )
    op.create_index("ix_questions_version", "procurement_questions", ["question_set_version_id"])

    op.create_table(
        "procurement_question_page_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("procurement_questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_id", sa.Integer(), sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position >= 1", name="ck_question_page_position_positive"),
        sa.UniqueConstraint("question_id", "page_id", name="uq_question_page_mapping"),
        sa.UniqueConstraint("question_id", "position", name="uq_question_page_position"),
    )
    op.create_index("ix_question_page_mapping_page", "procurement_question_page_mappings", ["page_id"])


def downgrade() -> None:
    op.drop_index("ix_question_page_mapping_page", table_name="procurement_question_page_mappings")
    op.drop_table("procurement_question_page_mappings")
    op.drop_index("ix_questions_version", table_name="procurement_questions")
    op.drop_table("procurement_questions")
    op.drop_table("procurement_question_set_versions")
    op.drop_index("ix_question_set_workspace_site", table_name="procurement_question_sets")
    op.drop_table("procurement_question_sets")
    with op.batch_alter_table("sites") as batch_op:
        batch_op.drop_constraint("uq_site_workspace_id", type_="unique")
