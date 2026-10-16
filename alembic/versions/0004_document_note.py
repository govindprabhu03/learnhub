"""documents.note: live processing progress and caveats shown to the user (e.g. OCR progress)

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("note", sa.Text))


def downgrade():
    op.drop_column("documents", "note")
