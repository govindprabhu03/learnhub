"""durable background job queue

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "jobs", sa.Column("id", sa.Integer, primary_key=True), sa.Column("kind", sa.Text, nullable=False),
        sa.Column("payload", sa.Text, nullable=False, server_default="{}"),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),  # queued | running | done | failed
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")), sa.Column("error", sa.Text),
        sa.Column("run_after", sa.Float, nullable=False, server_default=sa.text("0")),  # epoch seconds: retry backoff
        sa.Column("locked_at", sa.Float),  # epoch seconds: when a worker claimed it (stale -> presumed dead)
        sa.Column("created", sa.Text, server_default=sa.text("CURRENT_TIMESTAMP")))
    op.create_index("ix_jobs_claim", "jobs", ["status", "run_after"])


def downgrade():
    op.drop_table("jobs")
