"""backfill comisionario notas sucursal

Revision ID: e5f6a7b8c9d0
Revises: e4f5a6b7c8d9
Create Date: 2026-03-29 20:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    def distinct_sucursales_for_comisionario(comisionario_id: int) -> list[int]:
        rows = bind.execute(
            sa.text(
                """
                SELECT DISTINCT sucursal_id
                FROM comisionario_notas
                WHERE comisionario_id = :comisionario_id
                  AND sucursal_id IS NOT NULL
                ORDER BY sucursal_id
                """
            ),
            {"comisionario_id": comisionario_id},
        ).fetchall()
        return [row[0] for row in rows if row[0] is not None]

    default_sucursal_id = bind.execute(
        sa.text("SELECT id FROM sucursales ORDER BY id LIMIT 1")
    ).scalar()

    rows = bind.execute(
        sa.text(
            """
            SELECT id, comisionario_id, admin_id
            FROM comisionario_notas
            WHERE sucursal_id IS NULL
            ORDER BY id
            """
        )
    ).fetchall()

    for nota_id, comisionario_id, admin_id in rows:
        sucursal_id = None
        sucursales = distinct_sucursales_for_comisionario(comisionario_id)
        if len(sucursales) == 1:
            sucursal_id = sucursales[0]
        elif admin_id is not None:
            sucursal_id = bind.execute(
                sa.text("SELECT sucursal_id FROM users WHERE id = :admin_id"),
                {"admin_id": admin_id},
            ).scalar()
        if sucursal_id is None:
            sucursal_id = default_sucursal_id
        if sucursal_id is not None:
            bind.execute(
                sa.text(
                    """
                    UPDATE comisionario_notas
                    SET sucursal_id = :sucursal_id
                    WHERE id = :nota_id
                    """
                ),
                {"sucursal_id": sucursal_id, "nota_id": nota_id},
            )

    with op.batch_alter_table("comisionario_notas") as batch:
        batch.alter_column("sucursal_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("comisionario_notas") as batch:
        batch.alter_column("sucursal_id", existing_type=sa.Integer(), nullable=True)
