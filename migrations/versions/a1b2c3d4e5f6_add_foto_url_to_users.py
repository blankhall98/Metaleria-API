"""add foto_url to users

Revision ID: a1b2c3d4e5f6
Revises: ff5a6b7c8d90
Create Date: 2026-08-06 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "ff5a6b7c8d90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("foto_url", sa.String(500), nullable=True))


def downgrade():
    op.drop_column("users", "foto_url")
