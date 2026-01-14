"""add comision tipo_operacion and nota_origen_id

Revision ID: f6a7b8c9d0e1
Revises: f5e6a7b8c9d0
Create Date: 2026-01-10 14:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f6a7b8c9d0e1"
down_revision = "f5e6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector.get_columns(table))

    if dialect == "postgresql":
        op.execute("ALTER TYPE tipo_operacion ADD VALUE IF NOT EXISTS 'comision'")

    if dialect == "sqlite":
        with op.batch_alter_table("notas") as batch_op:
            if not has_column("notas", "nota_origen_id"):
                batch_op.add_column(sa.Column("nota_origen_id", sa.Integer(), nullable=True))
            batch_op.alter_column(
                "tipo_operacion",
                existing_type=sa.Enum("compra", "venta", name="tipo_operacion"),
                type_=sa.Enum("compra", "venta", "comision", name="tipo_operacion"),
            )
            batch_op.create_foreign_key(
                "fk_notas_nota_origen_id_notas",
                "notas",
                ["nota_origen_id"],
                ["id"],
            )
    else:
        if not has_column("notas", "nota_origen_id"):
            op.add_column("notas", sa.Column("nota_origen_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_notas_nota_origen_id_notas",
            "notas",
            "notas",
            ["nota_origen_id"],
            ["id"],
        )

    op.create_index("ix_notas_nota_origen_id", "notas", ["nota_origen_id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector.get_columns(table))

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector.get_indexes(table))

    if has_index("notas", "ix_notas_nota_origen_id"):
        op.drop_index("ix_notas_nota_origen_id", table_name="notas")

    if dialect == "sqlite":
        with op.batch_alter_table("notas") as batch_op:
            batch_op.drop_constraint("fk_notas_nota_origen_id_notas", type_="foreignkey")
            if has_column("notas", "nota_origen_id"):
                batch_op.drop_column("nota_origen_id")
            batch_op.alter_column(
                "tipo_operacion",
                existing_type=sa.Enum("compra", "venta", "comision", name="tipo_operacion"),
                type_=sa.Enum("compra", "venta", name="tipo_operacion"),
            )
    else:
        op.drop_constraint("fk_notas_nota_origen_id_notas", "notas", type_="foreignkey")
        if has_column("notas", "nota_origen_id"):
            op.drop_column("notas", "nota_origen_id")

    if dialect == "postgresql":
        # No se elimina el valor del enum en downgrade (limitación de Postgres).
        pass
