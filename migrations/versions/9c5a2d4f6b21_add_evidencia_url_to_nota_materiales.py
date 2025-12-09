"""add evidencia_url to nota_materiales

Revision ID: 9c5a2d4f6b21
Revises: 8f3c1b7a3d2c
Create Date: 2025-12-10 06:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9c5a2d4f6b21"
down_revision: Union[str, Sequence[str], None] = "8f3c1b7a3d2c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add evidencia_url column if not exists."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("nota_materiales")} if inspector.has_table("nota_materiales") else set()
    if "evidencia_url" not in cols:
        op.add_column("nota_materiales", sa.Column("evidencia_url", sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Drop evidencia_url column."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("nota_materiales")} if inspector.has_table("nota_materiales") else set()
    if "evidencia_url" in cols:
        op.drop_column("nota_materiales", "evidencia_url")
