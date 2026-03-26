"""link manual inventory adjustments to notes

Revision ID: c1d2e3f4a5b6
Revises: a9d8c7b6e5f4
Create Date: 2026-03-26 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "c1d2e3f4a5b6"
down_revision = "a9d8c7b6e5f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    def inspector():
        return sa.inspect(bind)

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector().get_columns(table))

    def has_index(table: str, index_name: str) -> bool:
        return any(idx.get("name") == index_name for idx in inspector().get_indexes(table))

    def has_fk(table: str, fk_name: str) -> bool:
        return any(fk.get("name") == fk_name for fk in inspector().get_foreign_keys(table))

    with op.batch_alter_table("inventario_ajustes_manuales") as batch:
        if not has_column("inventario_ajustes_manuales", "nota_id"):
            batch.add_column(sa.Column("nota_id", sa.Integer(), nullable=True))
        if not has_column("inventario_ajustes_manuales", "nota_material_id"):
            batch.add_column(sa.Column("nota_material_id", sa.Integer(), nullable=True))

    if not has_index("inventario_ajustes_manuales", "ix_inventario_ajustes_manuales_nota_id"):
        op.create_index(
            "ix_inventario_ajustes_manuales_nota_id",
            "inventario_ajustes_manuales",
            ["nota_id"],
            unique=False,
        )
    if not has_index("inventario_ajustes_manuales", "ix_inventario_ajustes_manuales_nota_material_id"):
        op.create_index(
            "ix_inventario_ajustes_manuales_nota_material_id",
            "inventario_ajustes_manuales",
            ["nota_material_id"],
            unique=False,
        )

    with op.batch_alter_table("inventario_ajustes_manuales") as batch:
        if not has_fk("inventario_ajustes_manuales", "fk_inv_ajustes_manuales_nota_id"):
            batch.create_foreign_key(
                "fk_inv_ajustes_manuales_nota_id",
                "notas",
                ["nota_id"],
                ["id"],
            )
        if not has_fk("inventario_ajustes_manuales", "fk_inv_ajustes_manuales_nota_material_id"):
            batch.create_foreign_key(
                "fk_inv_ajustes_manuales_nota_material_id",
                "nota_materiales",
                ["nota_material_id"],
                ["id"],
            )


def downgrade() -> None:
    bind = op.get_bind()

    def inspector():
        return sa.inspect(bind)

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector().get_columns(table))

    def has_index(table: str, index_name: str) -> bool:
        return any(idx.get("name") == index_name for idx in inspector().get_indexes(table))

    def has_fk(table: str, fk_name: str) -> bool:
        return any(fk.get("name") == fk_name for fk in inspector().get_foreign_keys(table))

    if has_index("inventario_ajustes_manuales", "ix_inventario_ajustes_manuales_nota_material_id"):
        op.drop_index(
            "ix_inventario_ajustes_manuales_nota_material_id",
            table_name="inventario_ajustes_manuales",
        )
    if has_index("inventario_ajustes_manuales", "ix_inventario_ajustes_manuales_nota_id"):
        op.drop_index(
            "ix_inventario_ajustes_manuales_nota_id",
            table_name="inventario_ajustes_manuales",
        )

    with op.batch_alter_table("inventario_ajustes_manuales") as batch:
        if has_fk("inventario_ajustes_manuales", "fk_inv_ajustes_manuales_nota_material_id"):
            batch.drop_constraint("fk_inv_ajustes_manuales_nota_material_id", type_="foreignkey")
        if has_fk("inventario_ajustes_manuales", "fk_inv_ajustes_manuales_nota_id"):
            batch.drop_constraint("fk_inv_ajustes_manuales_nota_id", type_="foreignkey")
        if has_column("inventario_ajustes_manuales", "nota_material_id"):
            batch.drop_column("nota_material_id")
        if has_column("inventario_ajustes_manuales", "nota_id"):
            batch.drop_column("nota_id")
