"""Tratos de venta de contenedores (punto 3, fase 2)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None

_ESTADO = sa.Enum("ABIERTO", "COMPLETADO", name="trato_venta_estado")


def upgrade() -> None:
    op.create_table(
        "tratos_venta",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("clientes.id"), nullable=False),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materiales.id"), nullable=False),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("contrato", sa.String(length=100), nullable=True),
        sa.Column("fecha_po", sa.Date(), nullable=True),
        sa.Column("fecha_vencimiento", sa.Date(), nullable=True),
        sa.Column("kg_tratados", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("premio_pct", sa.Numeric(6, 3), nullable=False, server_default="5.5"),
        sa.Column("comentarios", sa.String(length=500), nullable=True),
        sa.Column("estado", _ESTADO, nullable=False, server_default="ABIERTO"),
        sa.Column("completado_at", sa.DateTime(), nullable=True),
        sa.Column(
            "completado_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tratos_venta_cliente_id", "tratos_venta", ["cliente_id"])
    op.create_index("ix_tratos_venta_material_id", "tratos_venta", ["material_id"])
    op.create_index("ix_tratos_venta_estado", "tratos_venta", ["estado"])

    op.create_table(
        "tratos_venta_contenedores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trato_id", sa.Integer(), sa.ForeignKey("tratos_venta.id"), nullable=False),
        sa.Column("orden", sa.Integer(), nullable=True),
        sa.Column("numero_contenedor", sa.String(length=60), nullable=True),
        sa.Column("fecha_carga", sa.Date(), nullable=True),
        sa.Column("kg", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("peso_bascula_publica", sa.Numeric(14, 3), nullable=True),
        sa.Column("peso_puerto", sa.Numeric(14, 3), nullable=True),
        sa.Column("lme_usd_ton", sa.Numeric(12, 5), nullable=True),
        sa.Column("descuento_factor", sa.Numeric(8, 5), nullable=True),
        sa.Column("precio_lb_usd", sa.Numeric(12, 5), nullable=True),
        sa.Column("tc1", sa.Numeric(10, 4), nullable=True),
        sa.Column("usd_tc1", sa.Numeric(14, 2), nullable=True),
        sa.Column("tc2", sa.Numeric(10, 4), nullable=True),
        sa.Column("usd_tc2", sa.Numeric(14, 2), nullable=True),
        sa.Column("premio_pct", sa.Numeric(6, 3), nullable=True),
        sa.Column("comentarios", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_tratos_venta_contenedores_trato_id", "tratos_venta_contenedores", ["trato_id"]
    )

    op.create_table(
        "tratos_venta_notas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trato_id", sa.Integer(), sa.ForeignKey("tratos_venta.id"), nullable=False),
        sa.Column("nota_id", sa.Integer(), sa.ForeignKey("notas.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("nota_id", name="uq_tratos_venta_notas_nota_id"),
    )
    op.create_index("ix_tratos_venta_notas_trato_id", "tratos_venta_notas", ["trato_id"])
    op.create_index("ix_tratos_venta_notas_nota_id", "tratos_venta_notas", ["nota_id"])


def downgrade() -> None:
    op.drop_index("ix_tratos_venta_notas_nota_id", "tratos_venta_notas")
    op.drop_index("ix_tratos_venta_notas_trato_id", "tratos_venta_notas")
    op.drop_table("tratos_venta_notas")
    op.drop_index("ix_tratos_venta_contenedores_trato_id", "tratos_venta_contenedores")
    op.drop_table("tratos_venta_contenedores")
    op.drop_index("ix_tratos_venta_estado", "tratos_venta")
    op.drop_index("ix_tratos_venta_material_id", "tratos_venta")
    op.drop_index("ix_tratos_venta_cliente_id", "tratos_venta")
    op.drop_table("tratos_venta")
    _ESTADO.drop(op.get_bind(), checkfirst=True)
