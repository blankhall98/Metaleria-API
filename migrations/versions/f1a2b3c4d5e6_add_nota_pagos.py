"""add nota_pagos table

Revision ID: f1a2b3c4d5e6
Revises: e2f4a6b8c0d1
Create Date: 2025-12-22 22:05:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e2f4a6b8c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nota_pagos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nota_id", sa.Integer(), sa.ForeignKey("notas.id"), nullable=False),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("monto", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("metodo_pago", sa.String(length=50), nullable=True),
        sa.Column("cuenta_financiera", sa.String(length=100), nullable=True),
        sa.Column("comentario", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_nota_pagos_nota_id", "nota_pagos", ["nota_id"])
    op.create_index("ix_nota_pagos_usuario_id", "nota_pagos", ["usuario_id"])


def downgrade() -> None:
    op.drop_index("ix_nota_pagos_usuario_id", table_name="nota_pagos")
    op.drop_index("ix_nota_pagos_nota_id", table_name="nota_pagos")
    op.drop_table("nota_pagos")
