"""add inventory sucursal to notas

Revision ID: b1c3d5e7f9a1
Revises: a1b2c3d4e5f7
Create Date: 2026-04-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b1c3d5e7f9a1"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notas", schema=None) as batch_op:
        batch_op.add_column(sa.Column("inventario_sucursal_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_notas_inventario_sucursal_id", ["inventario_sucursal_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_notas_inventario_sucursal_id_sucursales",
            "sucursales",
            ["inventario_sucursal_id"],
            ["id"],
        )

    op.execute("UPDATE notas SET inventario_sucursal_id = sucursal_id WHERE inventario_sucursal_id IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("notas", schema=None) as batch_op:
        batch_op.drop_constraint("fk_notas_inventario_sucursal_id_sucursales", type_="foreignkey")
        batch_op.drop_index("ix_notas_inventario_sucursal_id")
        batch_op.drop_column("inventario_sucursal_id")
