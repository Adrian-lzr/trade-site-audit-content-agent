"""Persist model call accounting and budget reservations."""

from alembic import op
import sqlalchemy as sa


revision = "0018_model_call_budget_accounting"
down_revision = "0017_page_snapshot_parser_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_key", sa.String(255), nullable=False),
        sa.Column("call_type", sa.String(40), nullable=False),
        sa.Column("generation_id", sa.String(200), nullable=True),
        sa.Column("sample_id", sa.String(120), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("provider_request_id", sa.String(255), nullable=True),
        sa.Column("output_hash", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(18, 6), nullable=True),
        sa.Column("currency", sa.String(12), nullable=False, server_default="USD"),
        sa.Column("price_version", sa.String(120), nullable=True),
        sa.Column("cost_source", sa.String(80), nullable=True),
        sa.Column("cost_known", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="reserved"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "call_key", name="uq_model_call_workspace_key"),
        sa.CheckConstraint("status IN ('reserved', 'succeeded', 'failed', 'unknown_result')", name="ck_model_call_status"),
        sa.CheckConstraint("cost_known IN (0, 1)", name="ck_model_call_cost_known"),
    )
    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reservation_key", sa.String(255), nullable=False),
        sa.Column("model_call_id", sa.Integer(), sa.ForeignKey("model_calls.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reserved_amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("settled_amount", sa.Numeric(18, 6), nullable=True),
        sa.Column("currency", sa.String(12), nullable=False, server_default="USD"),
        sa.Column("status", sa.String(24), nullable=False, server_default="reserved"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "reservation_key", name="uq_budget_reservation_workspace_key"),
        sa.CheckConstraint("status IN ('reserved', 'settled', 'released', 'unknown_result')", name="ck_budget_reservation_status"),
    )


def downgrade() -> None:
    op.drop_table("budget_reservations")
    op.drop_table("model_calls")
