"""expand price precision to 5 decimals

Revision ID: f5e6a7b8c9d0
Revises: f4d5e6f7a8b9
Create Date: 2026-01-10 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f5e6a7b8c9d0"
down_revision = "f4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    def has_table(name: str) -> bool:
        return inspector.has_table(name)

    def has_column(table: str, column: str) -> bool:
        if not has_table(table):
            return False
        return any(col["name"] == column for col in inspector.get_columns(table))

    def alter_column(table: str, column: str, existing, new):
        if not has_column(table, column):
            return
        if dialect == "sqlite":
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column(column, existing_type=existing, type_=new)
        else:
            op.alter_column(table, column, existing_type=existing, type_=new)

    alter_column(
        "tablas_precios",
        "precio_por_unidad",
        sa.Numeric(10, 2),
        sa.Numeric(10, 5),
    )
    alter_column(
        "price_change_logs",
        "old_precio_por_unidad",
        sa.Numeric(10, 2),
        sa.Numeric(10, 5),
    )
    alter_column(
        "price_change_logs",
        "new_precio_por_unidad",
        sa.Numeric(10, 2),
        sa.Numeric(10, 5),
    )
    alter_column(
        "nota_materiales",
        "precio_unitario",
        sa.Numeric(12, 2),
        sa.Numeric(12, 5),
    )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    def has_table(name: str) -> bool:
        return inspector.has_table(name)

    def has_column(table: str, column: str) -> bool:
        if not has_table(table):
            return False
        return any(col["name"] == column for col in inspector.get_columns(table))

    def alter_column(table: str, column: str, existing, new):
        if not has_column(table, column):
            return
        if dialect == "sqlite":
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column(column, existing_type=existing, type_=new)
        else:
            op.alter_column(table, column, existing_type=existing, type_=new)

    alter_column(
        "tablas_precios",
        "precio_por_unidad",
        sa.Numeric(10, 5),
        sa.Numeric(10, 2),
    )
    alter_column(
        "price_change_logs",
        "old_precio_por_unidad",
        sa.Numeric(10, 5),
        sa.Numeric(10, 2),
    )
    alter_column(
        "price_change_logs",
        "new_precio_por_unidad",
        sa.Numeric(10, 5),
        sa.Numeric(10, 2),
    )
    alter_column(
        "nota_materiales",
        "precio_unitario",
        sa.Numeric(12, 5),
        sa.Numeric(12, 2),
    )
