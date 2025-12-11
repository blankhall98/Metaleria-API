"""add tables for multiple placas

Revision ID: d4e6f7a8b9c0
Revises: c7a1b2c3d4e5
Create Date: 2025-12-10 04:30:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d4e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c7a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "proveedor_placas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("proveedor_id", sa.Integer(), sa.ForeignKey("proveedores.id"), nullable=False),
        sa.Column("placa", sa.String(length=50), nullable=False),
    )
    op.create_index("ix_proveedor_placa", "proveedor_placas", ["placa"], unique=True)
    op.create_index("ix_proveedor_placas_proveedor_id", "proveedor_placas", ["proveedor_id"])

    op.create_table(
        "cliente_placas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("clientes.id"), nullable=False),
        sa.Column("placa", sa.String(length=50), nullable=False),
    )
    op.create_index("ix_cliente_placa", "cliente_placas", ["placa"], unique=True)
    op.create_index("ix_cliente_placas_cliente_id", "cliente_placas", ["cliente_id"])


def downgrade() -> None:
    op.drop_index("ix_cliente_placa", table_name="cliente_placas")
    op.drop_index("ix_cliente_placas_cliente_id", table_name="cliente_placas")
    op.drop_table("cliente_placas")

    op.drop_index("ix_proveedor_placa", table_name="proveedor_placas")
    op.drop_index("ix_proveedor_placas_proveedor_id", table_name="proveedor_placas")
    op.drop_table("proveedor_placas")

