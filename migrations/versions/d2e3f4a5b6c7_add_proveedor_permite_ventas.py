"""add provider direct sales flag

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-03-26 00:00:01
"""

from alembic import op
import sqlalchemy as sa


revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    def inspector():
        return sa.inspect(bind)

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector().get_columns(table))

    with op.batch_alter_table("proveedores") as batch:
        if not has_column("proveedores", "permite_ventas"):
            batch.add_column(
                sa.Column(
                    "permite_ventas",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )

    bind.execute(
        sa.text(
            """
            UPDATE proveedores
            SET permite_ventas = 1
            WHERE linked_cliente_id IS NOT NULL
               OR id IN (
                    SELECT linked_proveedor_id
                    FROM clientes
                    WHERE linked_proveedor_id IS NOT NULL
               )
               OR id IN (
                    SELECT proveedor_id
                    FROM notas
                    WHERE proveedor_id IS NOT NULL
                      AND tipo_operacion = 'venta'
               )
            """
        )
    )

    with op.batch_alter_table("proveedores") as batch:
        batch.alter_column("permite_ventas", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()

    def inspector():
        return sa.inspect(bind)

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector().get_columns(table))

    with op.batch_alter_table("proveedores") as batch:
        if has_column("proveedores", "permite_ventas"):
            batch.drop_column("permite_ventas")
