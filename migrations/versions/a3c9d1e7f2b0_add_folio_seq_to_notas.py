"""add folio_seq to notas

Revision ID: a3c9d1e7f2b0
Revises: f1a2b3c4d5e6
Create Date: 2025-12-22 23:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3c9d1e7f2b0"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notas", sa.Column("folio_seq", sa.Integer(), nullable=True))
    op.create_index("ix_notas_folio_seq", "notas", ["folio_seq"])

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, sucursal_id, tipo_operacion "
            "FROM notas "
            "ORDER BY sucursal_id, tipo_operacion, id"
        )
    ).fetchall()
    counters: dict[tuple[int, str], int] = {}
    for row in rows:
        mapping = getattr(row, "_mapping", row)
        if isinstance(mapping, dict):
            nota_id = mapping["id"]
            sucursal_id = mapping["sucursal_id"]
            tipo_operacion = mapping["tipo_operacion"]
        else:
            nota_id = row[0]
            sucursal_id = row[1]
            tipo_operacion = row[2]
        key = (int(sucursal_id), str(tipo_operacion))
        counters[key] = counters.get(key, 0) + 1
        bind.execute(
            sa.text("UPDATE notas SET folio_seq = :seq WHERE id = :id"),
            {"seq": counters[key], "id": nota_id},
        )


def downgrade() -> None:
    op.drop_index("ix_notas_folio_seq", table_name="notas")
    op.drop_column("notas", "folio_seq")
