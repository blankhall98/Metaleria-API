"""add inventory valuation prices

Revision ID: e6f7a8b9c0d1
Revises: d3e4f5a6b7c8
Create Date: 2026-04-06 00:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e6f7a8b9c0d1"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventario_valor_precios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sucursal_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("precio_referencia", sa.Numeric(12, 5), nullable=False, server_default="0"),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["material_id"], ["materiales.id"]),
        sa.ForeignKeyConstraint(["sucursal_id"], ["sucursales.id"]),
        sa.ForeignKeyConstraint(["usuario_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sucursal_id", "material_id", name="uq_inventario_valor_precios_sucursal_material"),
    )
    op.create_index(op.f("ix_inventario_valor_precios_id"), "inventario_valor_precios", ["id"], unique=False)
    op.create_index(op.f("ix_inventario_valor_precios_material_id"), "inventario_valor_precios", ["material_id"], unique=False)
    op.create_index(op.f("ix_inventario_valor_precios_sucursal_id"), "inventario_valor_precios", ["sucursal_id"], unique=False)
    op.create_index(op.f("ix_inventario_valor_precios_usuario_id"), "inventario_valor_precios", ["usuario_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_inventario_valor_precios_usuario_id"), table_name="inventario_valor_precios")
    op.drop_index(op.f("ix_inventario_valor_precios_sucursal_id"), table_name="inventario_valor_precios")
    op.drop_index(op.f("ix_inventario_valor_precios_material_id"), table_name="inventario_valor_precios")
    op.drop_index(op.f("ix_inventario_valor_precios_id"), table_name="inventario_valor_precios")
    op.drop_table("inventario_valor_precios")
