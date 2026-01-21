"""add comisionarios and comisionario notas

Revision ID: faa1b2c3d4e5
Revises: f9b0c1d2e3f4
Create Date: 2026-01-14 06:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "faa1b2c3d4e5"
down_revision = "f9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_table(name: str) -> bool:
        return inspector.has_table(name)

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector.get_indexes(table))

    def has_column(table: str, column_name: str) -> bool:
        return any(col["name"] == column_name for col in inspector.get_columns(table))

    if not has_table("comisionarios"):
        op.create_table(
            "comisionarios",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nombre_completo", sa.String(length=200), nullable=False),
            sa.Column("telefono", sa.String(length=50), nullable=True),
            sa.Column("correo_electronico", sa.String(length=200), nullable=True),
            sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_comisionarios_nombre_completo", "comisionarios", ["nombre_completo"])
        op.create_index("ix_comisionarios_telefono", "comisionarios", ["telefono"])
        op.create_index("ix_comisionarios_correo_electronico", "comisionarios", ["correo_electronico"])

    if not has_table("comisionario_notas"):
        op.create_table(
            "comisionario_notas",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("comisionario_id", sa.Integer(), sa.ForeignKey("comisionarios.id"), nullable=False),
            sa.Column("sucursal_id", sa.Integer(), sa.ForeignKey("sucursales.id"), nullable=True),
            sa.Column("admin_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column(
                "estado",
                sa.Enum("BORRADOR", "APROBADA", "CANCELADA", name="comisionario_nota_estado"),
                nullable=False,
                server_default="APROBADA",
            ),
            sa.Column("total_kg", sa.Numeric(12, 3), nullable=False, server_default="0"),
            sa.Column("total_monto", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("monto_pagado", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("comentarios_admin", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_comisionario_notas_comisionario_id", "comisionario_notas", ["comisionario_id"])
        op.create_index("ix_comisionario_notas_sucursal_id", "comisionario_notas", ["sucursal_id"])
        op.create_index("ix_comisionario_notas_admin_id", "comisionario_notas", ["admin_id"])
        op.create_index("ix_comisionario_notas_estado", "comisionario_notas", ["estado"])

    if not has_table("comisionario_nota_materiales"):
        op.create_table(
            "comisionario_nota_materiales",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nota_id", sa.Integer(), sa.ForeignKey("comisionario_notas.id"), nullable=False),
            sa.Column("material_id", sa.Integer(), sa.ForeignKey("materiales.id"), nullable=False),
            sa.Column("kg_neto", sa.Numeric(12, 3), nullable=False, server_default="0"),
            sa.Column("precio_por_kg", sa.Numeric(12, 5), nullable=False, server_default="0"),
            sa.Column("subtotal", sa.Numeric(12, 2), nullable=False, server_default="0"),
        )
        op.create_index("ix_comisionario_nota_materiales_nota_id", "comisionario_nota_materiales", ["nota_id"])
        op.create_index("ix_comisionario_nota_materiales_material_id", "comisionario_nota_materiales", ["material_id"])

    if not has_table("comisionario_pagos"):
        op.create_table(
            "comisionario_pagos",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nota_id", sa.Integer(), sa.ForeignKey("comisionario_notas.id"), nullable=False),
            sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("cuenta_id", sa.Integer(), sa.ForeignKey("cuentas.id"), nullable=True),
            sa.Column("cuenta_scrap360_id", sa.Integer(), sa.ForeignKey("cuentas_scrap360.id"), nullable=True),
            sa.Column("monto", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("metodo_pago", sa.String(length=50), nullable=True),
            sa.Column("cuenta_financiera", sa.String(length=100), nullable=True),
            sa.Column("comentario", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_comisionario_pagos_nota_id", "comisionario_pagos", ["nota_id"])
        op.create_index("ix_comisionario_pagos_usuario_id", "comisionario_pagos", ["usuario_id"])
        op.create_index("ix_comisionario_pagos_cuenta_id", "comisionario_pagos", ["cuenta_id"])
        op.create_index("ix_comisionario_pagos_cuenta_scrap360_id", "comisionario_pagos", ["cuenta_scrap360_id"])

    if has_table("cuentas") and not has_column("cuentas", "comisionario_id"):
        op.add_column("cuentas", sa.Column("comisionario_id", sa.Integer(), nullable=True))
        if not has_index("cuentas", "ix_cuentas_comisionario_id"):
            op.create_index("ix_cuentas_comisionario_id", "cuentas", ["comisionario_id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_table(name: str) -> bool:
        return inspector.has_table(name)

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector.get_indexes(table))

    def has_column(table: str, column_name: str) -> bool:
        return any(col["name"] == column_name for col in inspector.get_columns(table))

    if has_table("cuentas") and has_column("cuentas", "comisionario_id"):
        if has_index("cuentas", "ix_cuentas_comisionario_id"):
            op.drop_index("ix_cuentas_comisionario_id", table_name="cuentas")
        op.drop_column("cuentas", "comisionario_id")

    if has_table("comisionario_pagos"):
        for idx in (
            "ix_comisionario_pagos_cuenta_scrap360_id",
            "ix_comisionario_pagos_cuenta_id",
            "ix_comisionario_pagos_usuario_id",
            "ix_comisionario_pagos_nota_id",
        ):
            if has_index("comisionario_pagos", idx):
                op.drop_index(idx, table_name="comisionario_pagos")
        op.drop_table("comisionario_pagos")

    if has_table("comisionario_nota_materiales"):
        for idx in (
            "ix_comisionario_nota_materiales_material_id",
            "ix_comisionario_nota_materiales_nota_id",
        ):
            if has_index("comisionario_nota_materiales", idx):
                op.drop_index(idx, table_name="comisionario_nota_materiales")
        op.drop_table("comisionario_nota_materiales")

    if has_table("comisionario_notas"):
        for idx in (
            "ix_comisionario_notas_estado",
            "ix_comisionario_notas_admin_id",
            "ix_comisionario_notas_sucursal_id",
            "ix_comisionario_notas_comisionario_id",
        ):
            if has_index("comisionario_notas", idx):
                op.drop_index(idx, table_name="comisionario_notas")
        op.drop_table("comisionario_notas")

    if has_table("comisionarios"):
        for idx in (
            "ix_comisionarios_correo_electronico",
            "ix_comisionarios_telefono",
            "ix_comisionarios_nombre_completo",
        ):
            if has_index("comisionarios", idx):
                op.drop_index(idx, table_name="comisionarios")
        op.drop_table("comisionarios")
