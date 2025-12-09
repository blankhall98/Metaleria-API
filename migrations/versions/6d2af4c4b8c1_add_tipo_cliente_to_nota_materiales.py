"""add tipo_cliente to nota_materiales

Revision ID: 6d2af4c4b8c1
Revises: 9c5a2d4f6b21
Create Date: 2025-12-10 06:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6d2af4c4b8c1"
down_revision: Union[str, Sequence[str], None] = "9c5a2d4f6b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add tipo_cliente column if not exists."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("nota_materiales")} if inspector.has_table("nota_materiales") else set()
    if "tipo_cliente" not in cols:
        tipo_cliente_enum = sa.Enum("regular", "mayorista", "menudeo", name="tipo_cliente")
        tipo_cliente_enum.create(bind, checkfirst=True)
        op.add_column("nota_materiales", sa.Column("tipo_cliente", tipo_cliente_enum, nullable=True))


def downgrade() -> None:
    """Drop tipo_cliente column."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("nota_materiales")} if inspector.has_table("nota_materiales") else set()
    if "tipo_cliente" in cols:
        op.drop_column("nota_materiales", "tipo_cliente")
    tipo_cliente_enum = sa.Enum("regular", "mayorista", "menudeo", name="tipo_cliente")
    tipo_cliente_enum.drop(bind, checkfirst=True)
