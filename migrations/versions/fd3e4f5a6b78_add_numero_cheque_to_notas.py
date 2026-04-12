"""add numero_cheque to notas

Revision ID: fd3e4f5a6b78
Revises: fc2d3e4f5a67
Create Date: 2026-04-12 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "fd3e4f5a6b78"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("notas", sa.Column("numero_cheque", sa.String(80), nullable=True))


def downgrade():
    op.drop_column("notas", "numero_cheque")
