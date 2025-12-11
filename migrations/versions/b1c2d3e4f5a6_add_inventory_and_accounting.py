"""add inventory and accounting tables

Revision ID: b1c2d3e4f5a6
Revises: 8f3c1b7a3d2c
Create Date: 2025-12-10 01:00:04.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b1c2d3e4f5a6"
down_revision = "1f2c6d7d5a9e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventarios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sucursal_id", sa.Integer(), sa.ForeignKey("sucursales.id"), nullable=False),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materiales.id"), nullable=False),
        sa.Column("stock_inicial", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("stock_actual", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_inventarios_sucursal_material", "inventarios", ["sucursal_id", "material_id"], unique=True)

    op.create_table(
        "inventario_movimientos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inventario_id", sa.Integer(), sa.ForeignKey("inventarios.id"), nullable=False),
        sa.Column("nota_id", sa.Integer(), sa.ForeignKey("notas.id"), nullable=True),
        sa.Column("nota_material_id", sa.Integer(), sa.ForeignKey("nota_materiales.id"), nullable=True),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("cantidad_kg", sa.Numeric(12, 3), nullable=False),
        sa.Column("saldo_resultante", sa.Numeric(12, 3), nullable=False),
        sa.Column("comentario", sa.String(length=255), nullable=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_inv_mov_inventario", "inventario_movimientos", ["inventario_id"])
    op.create_index("ix_inv_mov_nota", "inventario_movimientos", ["nota_id"])

    op.create_table(
        "movimientos_contables",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nota_id", sa.Integer(), sa.ForeignKey("notas.id"), nullable=True),
        sa.Column("sucursal_id", sa.Integer(), sa.ForeignKey("sucursales.id"), nullable=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("monto", sa.Numeric(12, 2), nullable=False),
        sa.Column("metodo_pago", sa.String(length=50), nullable=True),
        sa.Column("cuenta_financiera", sa.String(length=100), nullable=True),
        sa.Column("comentario", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_mov_contable_nota", "movimientos_contables", ["nota_id"])
    op.create_index("ix_mov_contable_sucursal", "movimientos_contables", ["sucursal_id"])


def downgrade():
    op.drop_index("ix_mov_contable_sucursal", table_name="movimientos_contables")
    op.drop_index("ix_mov_contable_nota", table_name="movimientos_contables")
    op.drop_table("movimientos_contables")

    op.drop_index("ix_inv_mov_nota", table_name="inventario_movimientos")
    op.drop_index("ix_inv_mov_inventario", table_name="inventario_movimientos")
    op.drop_table("inventario_movimientos")

    op.drop_index("ix_inventarios_sucursal_material", table_name="inventarios")
    op.drop_table("inventarios")
