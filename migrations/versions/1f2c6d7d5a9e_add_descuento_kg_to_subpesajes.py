"""add descuento_kg to subpesajes

Revision ID: 1f2c6d7d5a9e
Revises: 6d2af4c4b8c1
Create Date: 2025-12-10 07:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "1f2c6d7d5a9e"
down_revision: Union[str, Sequence[str], None] = "6d2af4c4b8c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add descuento_kg column if not exists."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("subpesajes")} if inspector.has_table("subpesajes") else set()
    if "descuento_kg" not in cols:
        op.add_column("subpesajes", sa.Column("descuento_kg", sa.Numeric(12, 3), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    """Drop descuento_kg column."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("subpesajes")} if inspector.has_table("subpesajes") else set()
    if "descuento_kg" in cols:
        op.drop_column("subpesajes", "descuento_kg")
