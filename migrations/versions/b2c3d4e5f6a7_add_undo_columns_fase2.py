"""add undo columns for fase 2 punto 12

Ajustes de saldo de socio y movimientos manuales de tesorería se deshacen con
registro compensatorio (reversal_of_id enlaza la reversa con el original);
pagos de comisionista y gastos/movimientos del corte se deshacen con zero-out
(el trío reverted_* deja el candado y la auditoría).

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-07 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ajustes_saldo_partner") as batch:
        batch.add_column(sa.Column("reversal_of_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("reverted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("reverted_by_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_ajustes_saldo_partner_reversal", "ajustes_saldo_partner",
            ["reversal_of_id"], ["id"],
        )
        batch.create_foreign_key(
            "fk_ajustes_saldo_partner_reverted_by", "users",
            ["reverted_by_user_id"], ["id"],
        )

    with op.batch_alter_table("comisionario_pagos") as batch:
        batch.add_column(sa.Column("reverted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("reverted_by_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_comisionario_pagos_reverted_by", "users",
            ["reverted_by_user_id"], ["id"],
        )

    with op.batch_alter_table("cuentas_scrap360_movimientos") as batch:
        batch.add_column(sa.Column("reversal_of_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("reverted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("reverted_by_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_cs360_mov_reversal", "cuentas_scrap360_movimientos",
            ["reversal_of_id"], ["id"],
        )
        batch.create_foreign_key(
            "fk_cs360_mov_reverted_by", "users",
            ["reverted_by_user_id"], ["id"],
        )

    with op.batch_alter_table("corte_caja_gastos") as batch:
        batch.add_column(sa.Column("reverted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("reverted_by_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_corte_gastos_reverted_by", "users",
            ["reverted_by_user_id"], ["id"],
        )

    with op.batch_alter_table("corte_caja_movimientos") as batch:
        batch.add_column(sa.Column("reverted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("reverted_by_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_corte_movs_reverted_by", "users",
            ["reverted_by_user_id"], ["id"],
        )


def downgrade():
    with op.batch_alter_table("corte_caja_movimientos") as batch:
        batch.drop_constraint("fk_corte_movs_reverted_by", type_="foreignkey")
        batch.drop_column("reverted_by_user_id")
        batch.drop_column("reverted_at")

    with op.batch_alter_table("corte_caja_gastos") as batch:
        batch.drop_constraint("fk_corte_gastos_reverted_by", type_="foreignkey")
        batch.drop_column("reverted_by_user_id")
        batch.drop_column("reverted_at")

    with op.batch_alter_table("cuentas_scrap360_movimientos") as batch:
        batch.drop_constraint("fk_cs360_mov_reverted_by", type_="foreignkey")
        batch.drop_constraint("fk_cs360_mov_reversal", type_="foreignkey")
        batch.drop_column("reverted_by_user_id")
        batch.drop_column("reverted_at")
        batch.drop_column("reversal_of_id")

    with op.batch_alter_table("comisionario_pagos") as batch:
        batch.drop_constraint("fk_comisionario_pagos_reverted_by", type_="foreignkey")
        batch.drop_column("reverted_by_user_id")
        batch.drop_column("reverted_at")

    with op.batch_alter_table("ajustes_saldo_partner") as batch:
        batch.drop_constraint("fk_ajustes_saldo_partner_reverted_by", type_="foreignkey")
        batch.drop_constraint("fk_ajustes_saldo_partner_reversal", type_="foreignkey")
        batch.drop_column("reverted_by_user_id")
        batch.drop_column("reverted_at")
        batch.drop_column("reversal_of_id")
