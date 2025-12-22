"""add factura fields to notas

Revision ID: c6f2b3a1d9e0
Revises: b7c2d1e9a0f4
Create Date: 2025-12-23 01:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6f2b3a1d9e0"
down_revision: Union[str, Sequence[str], None] = "b7c2d1e9a0f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notas", sa.Column("factura_url", sa.String(length=255), nullable=True))
    op.add_column("notas", sa.Column("factura_generada_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("notas", "factura_generada_at")
    op.drop_column("notas", "factura_url")
