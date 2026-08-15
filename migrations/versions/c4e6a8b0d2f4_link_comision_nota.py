"""Vínculo nota de pesaje ↔ nota de comisión (fase 2).

La comisión se puede generar al aprobar la nota; el vínculo permite cancelarla
o restaurarla como registro compensatorio cuando la nota cambia de estado.
NULL = nota de comisión capturada a mano (el camino manual no cambia).

Revision ID: c4e6a8b0d2f4
Revises: b3d5f7a9c1e2
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "c4e6a8b0d2f4"
down_revision = "b3d5f7a9c1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("comisionario_notas") as batch:
        batch.add_column(sa.Column("nota_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_comisionario_notas_nota_id", "notas", ["nota_id"], ["id"]
        )
    # Única: una nota genera a lo más una comisión automática (los NULL de las
    # notas manuales no chocan entre sí ni en SQLite ni en Postgres).
    op.create_index(
        "ix_comisionario_notas_nota_id",
        "comisionario_notas",
        ["nota_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_comisionario_notas_nota_id", table_name="comisionario_notas")
    with op.batch_alter_table("comisionario_notas") as batch:
        batch.drop_constraint("fk_comisionario_notas_nota_id", type_="foreignkey")
        batch.drop_column("nota_id")
