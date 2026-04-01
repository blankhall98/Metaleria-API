"""add sucursal to comisionarios

Revision ID: c2d4e6f8a0b1
Revises: b1c3d5e7f9a1
Create Date: 2026-04-01 22:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c2d4e6f8a0b1"
down_revision = "b1c3d5e7f9a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("comisionarios", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sucursal_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_comisionarios_sucursal_id", ["sucursal_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_comisionarios_sucursal_id_sucursales",
            "sucursales",
            ["sucursal_id"],
            ["id"],
        )

    bind = op.get_bind()
    default_sucursal_id = bind.execute(
        sa.text("SELECT id FROM sucursales ORDER BY id LIMIT 1")
    ).scalar()
    if default_sucursal_id is None:
        raise RuntimeError("No existe ninguna sucursal para asignar a los comisionarios.")

    comisionario_ids = bind.execute(
        sa.text("SELECT id FROM comisionarios WHERE sucursal_id IS NULL ORDER BY id")
    ).fetchall()
    for (comisionario_id,) in comisionario_ids:
        sucursal_id = bind.execute(
            sa.text(
                """
                SELECT sucursal_id
                FROM comisionario_notas
                WHERE comisionario_id = :comisionario_id
                  AND sucursal_id IS NOT NULL
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"comisionario_id": comisionario_id},
        ).scalar()
        if sucursal_id is None:
            sucursal_id = bind.execute(
                sa.text(
                    """
                    SELECT sucursal_id
                    FROM cuentas
                    WHERE comisionario_id = :comisionario_id
                      AND sucursal_id IS NOT NULL
                    ORDER BY updated_at DESC NULLS LAST, id DESC
                    LIMIT 1
                    """
                ),
                {"comisionario_id": comisionario_id},
            ).scalar()
        if sucursal_id is None:
            sucursal_id = default_sucursal_id
        bind.execute(
            sa.text(
                """
                UPDATE comisionarios
                SET sucursal_id = :sucursal_id
                WHERE id = :comisionario_id
                """
            ),
            {"sucursal_id": sucursal_id, "comisionario_id": comisionario_id},
        )

    with op.batch_alter_table("comisionarios", schema=None) as batch_op:
        batch_op.alter_column("sucursal_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("comisionarios", schema=None) as batch_op:
        batch_op.drop_constraint("fk_comisionarios_sucursal_id_sucursales", type_="foreignkey")
        batch_op.drop_index("ix_comisionarios_sucursal_id")
        batch_op.drop_column("sucursal_id")
