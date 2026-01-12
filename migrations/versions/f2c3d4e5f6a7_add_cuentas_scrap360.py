"""add cuentas scrap360 and movements

Revision ID: f2c3d4e5f6a7
Revises: e3a4b5c6d7e8
Create Date: 2026-01-11 00:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f2c3d4e5f6a7"
down_revision = "e3a4b5c6d7e8"
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

    bool_default = sa.text("1") if dialect == "sqlite" else sa.text("true")

    if not has_table("cuentas_scrap360"):
        op.create_table(
            "cuentas_scrap360",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nombre", sa.String(length=120), nullable=False),
            sa.Column("tipo", sa.String(length=20), nullable=False),
            sa.Column("saldo_inicial", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("saldo_actual", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("activo", sa.Boolean(), nullable=False, server_default=bool_default),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_cuentas_scrap360_id", "cuentas_scrap360", ["id"], unique=False)

    if not has_table("cuentas_scrap360_sucursales"):
        op.create_table(
            "cuentas_scrap360_sucursales",
            sa.Column("cuenta_id", sa.Integer(), sa.ForeignKey("cuentas_scrap360.id"), primary_key=True),
            sa.Column("sucursal_id", sa.Integer(), sa.ForeignKey("sucursales.id"), primary_key=True),
        )

    if not has_table("cuentas_scrap360_movimientos"):
        op.create_table(
            "cuentas_scrap360_movimientos",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("cuenta_id", sa.Integer(), sa.ForeignKey("cuentas_scrap360.id"), nullable=False),
            sa.Column("nota_id", sa.Integer(), sa.ForeignKey("notas.id"), nullable=True),
            sa.Column("nota_pago_id", sa.Integer(), sa.ForeignKey("nota_pagos.id"), nullable=True),
            sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("tipo", sa.String(length=20), nullable=False),
            sa.Column("monto", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("saldo_resultante", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("comentario", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_cuentas_scrap360_movimientos_id",
            "cuentas_scrap360_movimientos",
            ["id"],
            unique=False,
        )
        op.create_index(
            "ix_cuentas_scrap360_movimientos_cuenta_id",
            "cuentas_scrap360_movimientos",
            ["cuenta_id"],
            unique=False,
        )
        op.create_index(
            "ix_cuentas_scrap360_movimientos_nota_id",
            "cuentas_scrap360_movimientos",
            ["nota_id"],
            unique=False,
        )
        op.create_index(
            "ix_cuentas_scrap360_movimientos_nota_pago_id",
            "cuentas_scrap360_movimientos",
            ["nota_pago_id"],
            unique=False,
        )
        op.create_index(
            "ix_cuentas_scrap360_movimientos_usuario_id",
            "cuentas_scrap360_movimientos",
            ["usuario_id"],
            unique=False,
        )

    if not has_column("nota_pagos", "cuenta_scrap360_id"):
        if dialect == "sqlite":
            with op.batch_alter_table("nota_pagos") as batch:
                batch.add_column(sa.Column("cuenta_scrap360_id", sa.Integer(), nullable=True))
                if not has_fk("nota_pagos", "cuenta_scrap360_id", "cuentas_scrap360"):
                    batch.create_foreign_key(
                        "fk_nota_pagos_cuenta_scrap360_id_cuentas_scrap360",
                        "cuentas_scrap360",
                        ["cuenta_scrap360_id"],
                        ["id"],
                    )
                if not has_index("nota_pagos", "ix_nota_pagos_cuenta_scrap360_id"):
                    batch.create_index("ix_nota_pagos_cuenta_scrap360_id", ["cuenta_scrap360_id"])
        else:
            op.add_column("nota_pagos", sa.Column("cuenta_scrap360_id", sa.Integer(), nullable=True))
            if not has_fk("nota_pagos", "cuenta_scrap360_id", "cuentas_scrap360"):
                op.create_foreign_key(
                    "fk_nota_pagos_cuenta_scrap360_id_cuentas_scrap360",
                    "nota_pagos",
                    "cuentas_scrap360",
                    ["cuenta_scrap360_id"],
                    ["id"],
                )
            if not has_index("nota_pagos", "ix_nota_pagos_cuenta_scrap360_id"):
                op.create_index("ix_nota_pagos_cuenta_scrap360_id", "nota_pagos", ["cuenta_scrap360_id"])


def downgrade():
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

    if has_column("nota_pagos", "cuenta_scrap360_id"):
        if dialect == "sqlite":
            with op.batch_alter_table("nota_pagos") as batch:
                if has_index("nota_pagos", "ix_nota_pagos_cuenta_scrap360_id"):
                    batch.drop_index("ix_nota_pagos_cuenta_scrap360_id")
                if has_fk("nota_pagos", "cuenta_scrap360_id", "cuentas_scrap360"):
                    batch.drop_constraint(
                        "fk_nota_pagos_cuenta_scrap360_id_cuentas_scrap360",
                        type_="foreignkey",
                    )
                batch.drop_column("cuenta_scrap360_id")
        else:
            if has_index("nota_pagos", "ix_nota_pagos_cuenta_scrap360_id"):
                op.drop_index("ix_nota_pagos_cuenta_scrap360_id", table_name="nota_pagos")
            if has_fk("nota_pagos", "cuenta_scrap360_id", "cuentas_scrap360"):
                op.drop_constraint(
                    "fk_nota_pagos_cuenta_scrap360_id_cuentas_scrap360",
                    "nota_pagos",
                    type_="foreignkey",
                )
            op.drop_column("nota_pagos", "cuenta_scrap360_id")

    if has_table("cuentas_scrap360_movimientos"):
        op.drop_index("ix_cuentas_scrap360_movimientos_usuario_id", table_name="cuentas_scrap360_movimientos")
        op.drop_index("ix_cuentas_scrap360_movimientos_nota_pago_id", table_name="cuentas_scrap360_movimientos")
        op.drop_index("ix_cuentas_scrap360_movimientos_nota_id", table_name="cuentas_scrap360_movimientos")
        op.drop_index("ix_cuentas_scrap360_movimientos_cuenta_id", table_name="cuentas_scrap360_movimientos")
        op.drop_index("ix_cuentas_scrap360_movimientos_id", table_name="cuentas_scrap360_movimientos")
        op.drop_table("cuentas_scrap360_movimientos")

    if has_table("cuentas_scrap360_sucursales"):
        op.drop_table("cuentas_scrap360_sucursales")

    if has_table("cuentas_scrap360"):
        op.drop_index("ix_cuentas_scrap360_id", table_name="cuentas_scrap360")
        op.drop_table("cuentas_scrap360")
