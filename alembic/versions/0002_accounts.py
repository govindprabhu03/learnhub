"""email verification flag + Google identity on users

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("email_verified", sa.Integer, nullable=False, server_default=sa.text("0")))
    op.add_column("users", sa.Column("google_sub", sa.Text))
    op.create_index("ux_users_google_sub", "users", ["google_sub"], unique=True)  # NULLs do not collide
    op.execute("UPDATE users SET email_verified = 1")  # accounts that predate verification are trusted


def downgrade():
    op.drop_index("ux_users_google_sub", table_name="users")
    op.drop_column("users", "google_sub")
    op.drop_column("users", "email_verified")
