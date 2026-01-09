"""add cuentas and account links

Revision ID: e3a4b5c6d7e8
Revises: d1f4a9b2c3e4
Create Date: 2026-01-09 23:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e3a4b5c6d7e8"
down_revision = "d1f4a9b2c3e4"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    def has_table(name: str) -> bool:
        return inspector.has_table(name)

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector.get_columns(table))

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector.get_indexes(table))

    def has_fk(table: str, column: str, referred: str) -> bool:
        for fk in inspector.get_foreign_keys(table):
            cols = fk.get("constrained_columns") or []
            if column in cols and fk.get("referred_table") == referred:
                return True
        return False

    if not has_table("cuentas"):
        op.create_table(
            "cuentas",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nombre", sa.String(length=120), nullable=False),
            sa.Column("tipo", sa.String(length=30), nullable=True),
            sa.Column("banco", sa.String(length=120), nullable=True),
            sa.Column("numero", sa.String(length=80), nullable=True),
            sa.Column("clabe", sa.String(length=80), nullable=True),
            sa.Column("titular", sa.String(length=120), nullable=True),
            sa.Column("referencia", sa.String(length=120), nullable=True),
            sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("sucursal_id", sa.Integer(), sa.ForeignKey("sucursales.id"), nullable=True),
            sa.Column("cliente_id", sa.Integer(), sa.ForeignKey("clientes.id"), nullable=True),
            sa.Column("proveedor_id", sa.Integer(), sa.ForeignKey("proveedores.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not has_index("cuentas", "ix_cuentas_sucursal_id"):
        op.create_index("ix_cuentas_sucursal_id", "cuentas", ["sucursal_id"])
    if not has_index("cuentas", "ix_cuentas_cliente_id"):
        op.create_index("ix_cuentas_cliente_id", "cuentas", ["cliente_id"])
    if not has_index("cuentas", "ix_cuentas_proveedor_id"):
        op.create_index("ix_cuentas_proveedor_id", "cuentas", ["proveedor_id"])
    if not has_index("cuentas", "ix_cuentas_activo"):
        op.create_index("ix_cuentas_activo", "cuentas", ["activo"])

    mov_has_col = has_column("movimientos_contables", "cuenta_id")
    mov_has_fk = has_fk("movimientos_contables", "cuenta_id", "cuentas")
    if dialect == "sqlite":
        with op.batch_alter_table("movimientos_contables") as batch_op:
            if not mov_has_col:
                batch_op.add_column(sa.Column("cuenta_id", sa.Integer(), nullable=True))
            if not mov_has_fk:
                batch_op.create_foreign_key(
                    "fk_movimientos_contables_cuenta_id_cuentas",
                    "cuentas",
                    ["cuenta_id"],
                    ["id"],
                )
    else:
        if not mov_has_col:
            op.add_column("movimientos_contables", sa.Column("cuenta_id", sa.Integer(), nullable=True))
        if not mov_has_fk:
            op.create_foreign_key(
                "fk_movimientos_contables_cuenta_id_cuentas",
                "movimientos_contables",
                "cuentas",
                ["cuenta_id"],
                ["id"],
            )
    if not has_index("movimientos_contables", "ix_movimientos_contables_cuenta_id"):
        op.create_index("ix_movimientos_contables_cuenta_id", "movimientos_contables", ["cuenta_id"])

    pago_has_col = has_column("nota_pagos", "cuenta_id")
    pago_has_fk = has_fk("nota_pagos", "cuenta_id", "cuentas")
    if dialect == "sqlite":
        with op.batch_alter_table("nota_pagos") as batch_op:
            if not pago_has_col:
                batch_op.add_column(sa.Column("cuenta_id", sa.Integer(), nullable=True))
            if not pago_has_fk:
                batch_op.create_foreign_key(
                    "fk_nota_pagos_cuenta_id_cuentas",
                    "cuentas",
                    ["cuenta_id"],
                    ["id"],
                )
    else:
        if not pago_has_col:
            op.add_column("nota_pagos", sa.Column("cuenta_id", sa.Integer(), nullable=True))
        if not pago_has_fk:
            op.create_foreign_key(
                "fk_nota_pagos_cuenta_id_cuentas",
                "nota_pagos",
                "cuentas",
                ["cuenta_id"],
                ["id"],
            )
    if not has_index("nota_pagos", "ix_nota_pagos_cuenta_id"):
        op.create_index("ix_nota_pagos_cuenta_id", "nota_pagos", ["cuenta_id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector.get_indexes(table))

    if dialect == "sqlite":
        with op.batch_alter_table("nota_pagos") as batch_op:
            batch_op.drop_column("cuenta_id")
        with op.batch_alter_table("movimientos_contables") as batch_op:
            batch_op.drop_column("cuenta_id")
    else:
        op.drop_constraint("fk_nota_pagos_cuenta_id_cuentas", "nota_pagos", type_="foreignkey")
        op.drop_column("nota_pagos", "cuenta_id")
        op.drop_constraint("fk_movimientos_contables_cuenta_id_cuentas", "movimientos_contables", type_="foreignkey")
        op.drop_column("movimientos_contables", "cuenta_id")

    if has_index("nota_pagos", "ix_nota_pagos_cuenta_id"):
        op.drop_index("ix_nota_pagos_cuenta_id", table_name="nota_pagos")
    if has_index("movimientos_contables", "ix_movimientos_contables_cuenta_id"):
        op.drop_index("ix_movimientos_contables_cuenta_id", table_name="movimientos_contables")

    if has_index("cuentas", "ix_cuentas_proveedor_id"):
        op.drop_index("ix_cuentas_proveedor_id", table_name="cuentas")
    if has_index("cuentas", "ix_cuentas_cliente_id"):
        op.drop_index("ix_cuentas_cliente_id", table_name="cuentas")
    if has_index("cuentas", "ix_cuentas_sucursal_id"):
        op.drop_index("ix_cuentas_sucursal_id", table_name="cuentas")
    if has_index("cuentas", "ix_cuentas_activo"):
        op.drop_index("ix_cuentas_activo", table_name="cuentas")
    op.drop_table("cuentas")
