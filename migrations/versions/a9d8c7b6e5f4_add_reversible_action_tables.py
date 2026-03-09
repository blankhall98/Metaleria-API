"""add reversible action tracking tables

Revision ID: a9d8c7b6e5f4
Revises: fc2d3e4f5a67
Create Date: 2026-03-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "a9d8c7b6e5f4"
down_revision = "fc2d3e4f5a67"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nota_devoluciones_parciales",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nota_id", sa.Integer(), sa.ForeignKey("notas.id"), nullable=False),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("comentario", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_nota_devoluciones_parciales_nota_id",
        "nota_devoluciones_parciales",
        ["nota_id"],
        unique=False,
    )
    op.create_index(
        "ix_nota_devoluciones_parciales_usuario_id",
        "nota_devoluciones_parciales",
        ["usuario_id"],
        unique=False,
    )

    op.create_table(
        "nota_devoluciones_parciales_lineas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("devolucion_id", sa.Integer(), sa.ForeignKey("nota_devoluciones_parciales.id"), nullable=False),
        sa.Column("nota_material_id", sa.Integer(), sa.ForeignKey("nota_materiales.id"), nullable=False),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materiales.id"), nullable=True),
        sa.Column("kg_devolucion", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("precio_unitario_devolucion", sa.Numeric(12, 5), nullable=False, server_default="0"),
        sa.Column("monto_devolucion", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("reverted_at", sa.DateTime(), nullable=True),
        sa.Column("reverted_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("comentario_reversion", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_nota_devoluciones_parciales_lineas_devolucion_id",
        "nota_devoluciones_parciales_lineas",
        ["devolucion_id"],
        unique=False,
    )
    op.create_index(
        "ix_nota_devoluciones_parciales_lineas_nota_material_id",
        "nota_devoluciones_parciales_lineas",
        ["nota_material_id"],
        unique=False,
    )
    op.create_index(
        "ix_nota_devoluciones_parciales_lineas_material_id",
        "nota_devoluciones_parciales_lineas",
        ["material_id"],
        unique=False,
    )
    op.create_index(
        "ix_nota_devoluciones_parciales_lineas_reverted_by_user_id",
        "nota_devoluciones_parciales_lineas",
        ["reverted_by_user_id"],
        unique=False,
    )

    op.create_table(
        "nota_devoluciones_parciales_aplicaciones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("linea_id", sa.Integer(), sa.ForeignKey("nota_devoluciones_parciales_lineas.id"), nullable=False),
        sa.Column("subpesaje_id", sa.Integer(), sa.ForeignKey("subpesajes.id"), nullable=True),
        sa.Column("kg_aplicado", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_nota_devoluciones_parciales_aplicaciones_linea_id",
        "nota_devoluciones_parciales_aplicaciones",
        ["linea_id"],
        unique=False,
    )
    op.create_index(
        "ix_nota_devoluciones_parciales_aplicaciones_subpesaje_id",
        "nota_devoluciones_parciales_aplicaciones",
        ["subpesaje_id"],
        unique=False,
    )

    op.create_table(
        "nota_devoluciones_totales",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nota_id", sa.Integer(), sa.ForeignKey("notas.id"), nullable=False),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("comentario", sa.String(length=255), nullable=True),
        sa.Column("reverted_at", sa.DateTime(), nullable=True),
        sa.Column("reverted_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("comentario_reversion", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_nota_devoluciones_totales_nota_id",
        "nota_devoluciones_totales",
        ["nota_id"],
        unique=False,
    )
    op.create_index(
        "ix_nota_devoluciones_totales_usuario_id",
        "nota_devoluciones_totales",
        ["usuario_id"],
        unique=False,
    )
    op.create_index(
        "ix_nota_devoluciones_totales_reverted_by_user_id",
        "nota_devoluciones_totales",
        ["reverted_by_user_id"],
        unique=False,
    )

    op.create_table(
        "inventario_ajustes_manuales",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sucursal_id", sa.Integer(), sa.ForeignKey("sucursales.id"), nullable=False),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materiales.id"), nullable=False),
        sa.Column("inventario_movimiento_id", sa.Integer(), sa.ForeignKey("inventario_movimientos.id"), nullable=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("cantidad_kg", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("stock_anterior", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("stock_resultante", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("comentario", sa.String(length=255), nullable=True),
        sa.Column("reversal_of_id", sa.Integer(), sa.ForeignKey("inventario_ajustes_manuales.id"), nullable=True),
        sa.Column("reverted_at", sa.DateTime(), nullable=True),
        sa.Column("reverted_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("comentario_reversion", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_inventario_ajustes_manuales_sucursal_id",
        "inventario_ajustes_manuales",
        ["sucursal_id"],
        unique=False,
    )
    op.create_index(
        "ix_inventario_ajustes_manuales_material_id",
        "inventario_ajustes_manuales",
        ["material_id"],
        unique=False,
    )
    op.create_index(
        "ix_inventario_ajustes_manuales_inventario_movimiento_id",
        "inventario_ajustes_manuales",
        ["inventario_movimiento_id"],
        unique=False,
    )
    op.create_index(
        "ix_inventario_ajustes_manuales_usuario_id",
        "inventario_ajustes_manuales",
        ["usuario_id"],
        unique=False,
    )
    op.create_index(
        "ix_inventario_ajustes_manuales_reversal_of_id",
        "inventario_ajustes_manuales",
        ["reversal_of_id"],
        unique=False,
    )
    op.create_index(
        "ix_inventario_ajustes_manuales_reverted_by_user_id",
        "inventario_ajustes_manuales",
        ["reverted_by_user_id"],
        unique=False,
    )

    op.create_table(
        "conversiones_material_reversiones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversion_id", sa.Integer(), sa.ForeignKey("conversiones_material.id"), nullable=False),
        sa.Column("reversal_conversion_id", sa.Integer(), sa.ForeignKey("conversiones_material.id"), nullable=False),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("conversion_id", name="uq_conversiones_material_reversiones_conversion_id"),
        sa.UniqueConstraint("reversal_conversion_id", name="uq_conversiones_material_reversiones_reversal_conversion_id"),
    )
    op.create_index(
        "ix_conversiones_material_reversiones_conversion_id",
        "conversiones_material_reversiones",
        ["conversion_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversiones_material_reversiones_reversal_conversion_id",
        "conversiones_material_reversiones",
        ["reversal_conversion_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversiones_material_reversiones_usuario_id",
        "conversiones_material_reversiones",
        ["usuario_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_conversiones_material_reversiones_usuario_id", table_name="conversiones_material_reversiones")
    op.drop_index("ix_conversiones_material_reversiones_reversal_conversion_id", table_name="conversiones_material_reversiones")
    op.drop_index("ix_conversiones_material_reversiones_conversion_id", table_name="conversiones_material_reversiones")
    op.drop_table("conversiones_material_reversiones")

    op.drop_index("ix_inventario_ajustes_manuales_reverted_by_user_id", table_name="inventario_ajustes_manuales")
    op.drop_index("ix_inventario_ajustes_manuales_reversal_of_id", table_name="inventario_ajustes_manuales")
    op.drop_index("ix_inventario_ajustes_manuales_usuario_id", table_name="inventario_ajustes_manuales")
    op.drop_index("ix_inventario_ajustes_manuales_inventario_movimiento_id", table_name="inventario_ajustes_manuales")
    op.drop_index("ix_inventario_ajustes_manuales_material_id", table_name="inventario_ajustes_manuales")
    op.drop_index("ix_inventario_ajustes_manuales_sucursal_id", table_name="inventario_ajustes_manuales")
    op.drop_table("inventario_ajustes_manuales")

    op.drop_index("ix_nota_devoluciones_totales_reverted_by_user_id", table_name="nota_devoluciones_totales")
    op.drop_index("ix_nota_devoluciones_totales_usuario_id", table_name="nota_devoluciones_totales")
    op.drop_index("ix_nota_devoluciones_totales_nota_id", table_name="nota_devoluciones_totales")
    op.drop_table("nota_devoluciones_totales")

    op.drop_index("ix_nota_devoluciones_parciales_aplicaciones_subpesaje_id", table_name="nota_devoluciones_parciales_aplicaciones")
    op.drop_index("ix_nota_devoluciones_parciales_aplicaciones_linea_id", table_name="nota_devoluciones_parciales_aplicaciones")
    op.drop_table("nota_devoluciones_parciales_aplicaciones")

    op.drop_index("ix_nota_devoluciones_parciales_lineas_reverted_by_user_id", table_name="nota_devoluciones_parciales_lineas")
    op.drop_index("ix_nota_devoluciones_parciales_lineas_material_id", table_name="nota_devoluciones_parciales_lineas")
    op.drop_index("ix_nota_devoluciones_parciales_lineas_nota_material_id", table_name="nota_devoluciones_parciales_lineas")
    op.drop_index("ix_nota_devoluciones_parciales_lineas_devolucion_id", table_name="nota_devoluciones_parciales_lineas")
    op.drop_table("nota_devoluciones_parciales_lineas")

    op.drop_index("ix_nota_devoluciones_parciales_usuario_id", table_name="nota_devoluciones_parciales")
    op.drop_index("ix_nota_devoluciones_parciales_nota_id", table_name="nota_devoluciones_parciales")
    op.drop_table("nota_devoluciones_parciales")
