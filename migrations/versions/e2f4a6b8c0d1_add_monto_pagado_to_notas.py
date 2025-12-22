"""add monto_pagado to notas

Revision ID: e2f4a6b8c0d1
Revises: d4e6f7a8b9c0
Create Date: 2025-12-22 21:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f4a6b8c0d1"
down_revision: Union[str, Sequence[str], None] = "d4e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notas",
        sa.Column("monto_pagado", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE notas SET monto_pagado = total_monto "
        "WHERE lower(coalesce(metodo_pago, '')) = 'efectivo' "
        "AND estado = 'APROBADA'"
    )


def downgrade() -> None:
    op.drop_column("notas", "monto_pagado")
