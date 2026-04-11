"""add real kg fields to notes

Revision ID: e7f8a9b0c1d2
Revises: e6f7a8b9c0d1
Create Date: 2026-04-11 23:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e7f8a9b0c1d2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.add_column(
        "notas",
        sa.Column("total_kg_real", sa.Numeric(12, 3), nullable=False, server_default="0"),
    )
    op.add_column(
        "nota_materiales",
        sa.Column("kg_real", sa.Numeric(12, 3), nullable=False, server_default="0"),
    )
    op.add_column(
        "nota_devoluciones_parciales_lineas",
        sa.Column("kg_real_devolucion", sa.Numeric(12, 3), nullable=False, server_default="0"),
    )

    op.execute("UPDATE nota_materiales SET kg_real = COALESCE(kg_neto, 0)")
    op.execute("UPDATE notas SET total_kg_real = COALESCE(total_kg_neto, 0)")
    op.execute(
        "UPDATE nota_devoluciones_parciales_lineas "
        "SET kg_real_devolucion = COALESCE(kg_devolucion, 0)"
    )

    if not is_sqlite:
        op.alter_column("notas", "total_kg_real", server_default=None)
        op.alter_column("nota_materiales", "kg_real", server_default=None)
        op.alter_column("nota_devoluciones_parciales_lineas", "kg_real_devolucion", server_default=None)


def downgrade() -> None:
    op.drop_column("nota_devoluciones_parciales_lineas", "kg_real_devolucion")
    op.drop_column("nota_materiales", "kg_real")
    op.drop_column("notas", "total_kg_real")
