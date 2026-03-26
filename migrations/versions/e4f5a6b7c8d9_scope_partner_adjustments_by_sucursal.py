"""scope partner balance adjustments by sucursal

Revision ID: e4f5a6b7c8d9
Revises: d2e3f4a5b6c7
Create Date: 2026-03-26 12:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e4f5a6b7c8d9"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    def inspector():
        return sa.inspect(bind)

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector().get_columns(table))

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector().get_indexes(table))

    with op.batch_alter_table("ajustes_saldo_partner") as batch:
        if not has_column("ajustes_saldo_partner", "sucursal_id"):
            batch.add_column(sa.Column("sucursal_id", sa.Integer(), nullable=True))

    if not has_index("ajustes_saldo_partner", "ix_ajustes_saldo_partner_sucursal_id"):
        op.create_index(
            "ix_ajustes_saldo_partner_sucursal_id",
            "ajustes_saldo_partner",
            ["sucursal_id"],
        )

    rows = bind.execute(
        sa.text(
            """
            SELECT id, partner_type, partner_id
            FROM ajustes_saldo_partner
            WHERE sucursal_id IS NULL
            ORDER BY id
            """
        )
    ).fetchall()
    for ajuste_id, partner_type, partner_id in rows:
        sucursal_id = None
        if partner_type == "cliente":
            sucursal_id = bind.execute(
                sa.text("SELECT sucursal_id FROM clientes WHERE id = :partner_id"),
                {"partner_id": partner_id},
            ).scalar()
        elif partner_type == "proveedor":
            sucursal_id = bind.execute(
                sa.text("SELECT sucursal_id FROM proveedores WHERE id = :partner_id"),
                {"partner_id": partner_id},
            ).scalar()
        if sucursal_id is not None:
            bind.execute(
                sa.text(
                    """
                    UPDATE ajustes_saldo_partner
                    SET sucursal_id = :sucursal_id
                    WHERE id = :ajuste_id
                    """
                ),
                {"sucursal_id": sucursal_id, "ajuste_id": ajuste_id},
            )


def downgrade() -> None:
    bind = op.get_bind()

    def inspector():
        return sa.inspect(bind)

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector().get_columns(table))

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector().get_indexes(table))

    if has_index("ajustes_saldo_partner", "ix_ajustes_saldo_partner_sucursal_id"):
        op.drop_index("ix_ajustes_saldo_partner_sucursal_id", table_name="ajustes_saldo_partner")

    with op.batch_alter_table("ajustes_saldo_partner") as batch:
        if has_column("ajustes_saldo_partner", "sucursal_id"):
            batch.drop_column("sucursal_id")
