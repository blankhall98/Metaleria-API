"""add caja sucursal fields and corte categories

Revision ID: d3e4f5a6b7c8
Revises: c2d4e6f8a0b1
Create Date: 2026-04-02 10:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d3e4f5a6b7c8"
down_revision = "c2d4e6f8a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("nota_pagos") as batch_op:
        batch_op.add_column(sa.Column("caja_sucursal_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_nota_pagos_caja_sucursal_id", ["caja_sucursal_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_nota_pagos_caja_sucursal_id_sucursales",
            "sucursales",
            ["caja_sucursal_id"],
            ["id"],
        )

    with op.batch_alter_table("movimientos_contables") as batch_op:
        batch_op.add_column(sa.Column("caja_sucursal_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_movimientos_contables_caja_sucursal_id", ["caja_sucursal_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_movimientos_contables_caja_sucursal_id_sucursales",
            "sucursales",
            ["caja_sucursal_id"],
            ["id"],
        )

    with op.batch_alter_table("corte_caja_movimientos") as batch_op:
        batch_op.add_column(sa.Column("categoria", sa.String(length=50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("corte_caja_movimientos") as batch_op:
        batch_op.drop_column("categoria")

    with op.batch_alter_table("movimientos_contables") as batch_op:
        batch_op.drop_constraint("fk_movimientos_contables_caja_sucursal_id_sucursales", type_="foreignkey")
        batch_op.drop_index("ix_movimientos_contables_caja_sucursal_id")
        batch_op.drop_column("caja_sucursal_id")

    with op.batch_alter_table("nota_pagos") as batch_op:
        batch_op.drop_constraint("fk_nota_pagos_caja_sucursal_id_sucursales", type_="foreignkey")
        batch_op.drop_index("ix_nota_pagos_caja_sucursal_id")
        batch_op.drop_column("caja_sucursal_id")
