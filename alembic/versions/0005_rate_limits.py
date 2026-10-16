"""shared rate-limit counters

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("rate_limits", sa.Column("k", sa.Text, primary_key=True),
                    sa.Column("window_start", sa.Float, nullable=False),  # epoch seconds
                    sa.Column("hits", sa.Integer, nullable=False))


def downgrade():
    op.drop_table("rate_limits")
