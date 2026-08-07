"""Bitácora de llamadas con proveedores (punto 2, fase 2)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None

_ESTATUS = sa.Enum(
    "PENDIENTE",
    "ENTREGADO",
    "NO_CONFIRMO",
    name="llamada_proveedor_estatus",
)


def upgrade() -> None:
    op.create_table(
        "llamadas_proveedor",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("proveedor_id", sa.Integer(), sa.ForeignKey("proveedores.id"), nullable=False),
        sa.Column("sucursal_id", sa.Integer(), sa.ForeignKey("sucursales.id"), nullable=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("fecha_estimada_entrega", sa.String(length=120), nullable=True),
        sa.Column("comentarios", sa.String(length=500), nullable=True),
        sa.Column("estatus", _ESTATUS, nullable=False, server_default="PENDIENTE"),
        sa.Column("entregada_at", sa.DateTime(), nullable=True),
        sa.Column("entregada_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_llamadas_proveedor_proveedor_id", "llamadas_proveedor", ["proveedor_id"])
    op.create_index("ix_llamadas_proveedor_sucursal_id", "llamadas_proveedor", ["sucursal_id"])
    op.create_index("ix_llamadas_proveedor_fecha", "llamadas_proveedor", ["fecha"])
    op.create_index("ix_llamadas_proveedor_estatus", "llamadas_proveedor", ["estatus"])

    op.create_table(
        "llamadas_proveedor_materiales",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "llamada_id",
            sa.Integer(),
            sa.ForeignKey("llamadas_proveedor.id"),
            nullable=False,
        ),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materiales.id"), nullable=True),
        sa.Column("precio_por_kg", sa.Numeric(12, 5), nullable=True),
        sa.Column("precio_texto", sa.String(length=120), nullable=True),
        sa.Column("kg_aproximados", sa.Numeric(12, 3), nullable=True),
    )
    op.create_index(
        "ix_llamadas_proveedor_materiales_llamada_id",
        "llamadas_proveedor_materiales",
        ["llamada_id"],
    )
    op.create_index(
        "ix_llamadas_proveedor_materiales_material_id",
        "llamadas_proveedor_materiales",
        ["material_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_llamadas_proveedor_materiales_material_id", "llamadas_proveedor_materiales")
    op.drop_index("ix_llamadas_proveedor_materiales_llamada_id", "llamadas_proveedor_materiales")
    op.drop_table("llamadas_proveedor_materiales")
    op.drop_index("ix_llamadas_proveedor_estatus", "llamadas_proveedor")
    op.drop_index("ix_llamadas_proveedor_fecha", "llamadas_proveedor")
    op.drop_index("ix_llamadas_proveedor_sucursal_id", "llamadas_proveedor")
    op.drop_index("ix_llamadas_proveedor_proveedor_id", "llamadas_proveedor")
    op.drop_table("llamadas_proveedor")
    _ESTATUS.drop(op.get_bind(), checkfirst=True)
