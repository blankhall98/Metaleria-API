"""add notas, materiales y subpesajes

Revision ID: 8f3c1b7a3d2c
Revises: 3f186aa7323f
Create Date: 2025-12-10 04:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "8f3c1b7a3d2c"
down_revision: Union[str, Sequence[str], None] = "3f186aa7323f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create notas core tables."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    nota_estado = postgresql.ENUM(
        "BORRADOR",
        "EN_REVISION",
        "APROBADA",
        "CANCELADA",
        name="nota_estado",
        create_type=False,
    )
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'nota_estado') THEN "
        "CREATE TYPE nota_estado AS ENUM ('BORRADOR', 'EN_REVISION', 'APROBADA', 'CANCELADA'); "
        "END IF; "
        "END $$;"
    )
    tipo_operacion = postgresql.ENUM("compra", "venta", name="tipo_operacion", create_type=False)

    if not inspector.has_table("notas"):
        op.create_table(
            "notas",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("sucursal_id", sa.Integer(), sa.ForeignKey("sucursales.id"), nullable=False),
            sa.Column("trabajador_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("admin_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("proveedor_id", sa.Integer(), sa.ForeignKey("proveedores.id"), nullable=True),
            sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("clientes.id"), nullable=True),
            sa.Column("tipo_operacion", tipo_operacion, nullable=False),
            sa.Column("estado", nota_estado, nullable=False),
            sa.Column("total_kg_bruto", sa.Numeric(12, 3), nullable=False, server_default=sa.text("0")),
            sa.Column("total_kg_descuento", sa.Numeric(12, 3), nullable=False, server_default=sa.text("0")),
            sa.Column("total_kg_neto", sa.Numeric(12, 3), nullable=False, server_default=sa.text("0")),
            sa.Column("total_monto", sa.Numeric(12, 2), nullable=False, server_default=sa.text("0")),
            sa.Column("metodo_pago", sa.String(length=50), nullable=True),
            sa.Column("cuenta_financiera_id", sa.Integer(), nullable=True),
            sa.Column("fecha_caducidad_pago", sa.Date(), nullable=True),
            sa.Column("comentarios_trabajador", sa.Text(), nullable=True),
            sa.Column("comentarios_admin", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )

    existing_notas_idx = {idx["name"] for idx in inspector.get_indexes("notas")} if inspector.has_table("notas") else set()
    if "ix_notas_sucursal_id" not in existing_notas_idx:
        op.create_index("ix_notas_sucursal_id", "notas", ["sucursal_id"])
    if "ix_notas_trabajador_id" not in existing_notas_idx:
        op.create_index("ix_notas_trabajador_id", "notas", ["trabajador_id"])
    if "ix_notas_admin_id" not in existing_notas_idx:
        op.create_index("ix_notas_admin_id", "notas", ["admin_id"])
    if "ix_notas_proveedor_id" not in existing_notas_idx:
        op.create_index("ix_notas_proveedor_id", "notas", ["proveedor_id"])
    if "ix_notas_cliente_id" not in existing_notas_idx:
        op.create_index("ix_notas_cliente_id", "notas", ["cliente_id"])
    if "ix_notas_estado" not in existing_notas_idx:
        op.create_index("ix_notas_estado", "notas", ["estado"])

    if not inspector.has_table("nota_materiales"):
        op.create_table(
            "nota_materiales",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nota_id", sa.Integer(), sa.ForeignKey("notas.id"), nullable=False),
            sa.Column("material_id", sa.Integer(), sa.ForeignKey("materiales.id"), nullable=False),
            sa.Column("kg_bruto", sa.Numeric(12, 3), nullable=False, server_default=sa.text("0")),
            sa.Column("kg_descuento", sa.Numeric(12, 3), nullable=False, server_default=sa.text("0")),
            sa.Column("kg_neto", sa.Numeric(12, 3), nullable=False, server_default=sa.text("0")),
            sa.Column("precio_unitario", sa.Numeric(12, 2), nullable=True),
            sa.Column("subtotal", sa.Numeric(12, 2), nullable=True),
            sa.Column("version_precio_id", sa.Integer(), sa.ForeignKey("tablas_precios.id"), nullable=True),
            sa.Column("orden", sa.Integer(), nullable=True),
        )

    existing_nm_idx = {idx["name"] for idx in inspector.get_indexes("nota_materiales")} if inspector.has_table("nota_materiales") else set()
    if "ix_nota_materiales_nota_id" not in existing_nm_idx:
        op.create_index("ix_nota_materiales_nota_id", "nota_materiales", ["nota_id"])
    if "ix_nota_materiales_material_id" not in existing_nm_idx:
        op.create_index("ix_nota_materiales_material_id", "nota_materiales", ["material_id"])

    if not inspector.has_table("subpesajes"):
        op.create_table(
            "subpesajes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nota_material_id", sa.Integer(), sa.ForeignKey("nota_materiales.id"), nullable=False),
            sa.Column("peso_kg", sa.Numeric(12, 3), nullable=False),
            sa.Column("foto_url", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    existing_sub_idx = {idx["name"] for idx in inspector.get_indexes("subpesajes")} if inspector.has_table("subpesajes") else set()
    if "ix_subpesajes_nota_material_id" not in existing_sub_idx:
        op.create_index("ix_subpesajes_nota_material_id", "subpesajes", ["nota_material_id"])

    if not inspector.has_table("nota_originales"):
        op.create_table(
            "nota_originales",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nota_id", sa.Integer(), sa.ForeignKey("notas.id"), nullable=False, unique=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    existing_no_idx = {idx["name"] for idx in inspector.get_indexes("nota_originales")} if inspector.has_table("nota_originales") else set()
    if "ix_nota_originales_nota_id" not in existing_no_idx:
        op.create_index("ix_nota_originales_nota_id", "nota_originales", ["nota_id"])


def downgrade() -> None:
    """Drop notas core tables."""
    op.drop_index("ix_nota_originales_nota_id", table_name="nota_originales")
    op.drop_table("nota_originales")

    op.drop_index("ix_subpesajes_nota_material_id", table_name="subpesajes")
    op.drop_table("subpesajes")

    op.drop_index("ix_nota_materiales_material_id", table_name="nota_materiales")
    op.drop_index("ix_nota_materiales_nota_id", table_name="nota_materiales")
    op.drop_table("nota_materiales")

    op.drop_index("ix_notas_cliente_id", table_name="notas")
    op.drop_index("ix_notas_proveedor_id", table_name="notas")
    op.drop_index("ix_notas_admin_id", table_name="notas")
    op.drop_index("ix_notas_trabajador_id", table_name="notas")
    op.drop_index("ix_notas_sucursal_id", table_name="notas")
    op.drop_index("ix_notas_estado", table_name="notas")
    op.drop_table("notas")

    nota_estado = postgresql.ENUM(
        "BORRADOR",
        "EN_REVISION",
        "APROBADA",
        "CANCELADA",
        name="nota_estado",
        create_type=False,
    )
    nota_estado.drop(op.get_bind(), checkfirst=True)
