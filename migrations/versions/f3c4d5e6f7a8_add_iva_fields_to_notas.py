"""add iva fields to notas

Revision ID: f3c4d5e6f7a8
Revises: f2c3d4e5f6a7
Create Date: 2026-01-13 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f3c4d5e6f7a8"
down_revision = "f2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector.get_columns(table))

    bool_default = sa.text("0") if dialect == "sqlite" else sa.text("false")

    if not has_column("notas", "iva_incluido"):
        op.add_column(
            "notas",
            sa.Column(
                "iva_incluido",
                sa.Boolean(),
                nullable=False,
                server_default=bool_default,
            ),
        )
    if not has_column("notas", "iva_porcentaje"):
        op.add_column(
            "notas",
            sa.Column(
                "iva_porcentaje",
                sa.Numeric(5, 2),
                nullable=False,
                server_default="16.00",
            ),
        )
    if not has_column("notas", "iva_monto"):
        op.add_column(
            "notas",
            sa.Column(
                "iva_monto",
                sa.Numeric(12, 2),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector.get_columns(table))

    if has_column("notas", "iva_monto"):
        op.drop_column("notas", "iva_monto")
    if has_column("notas", "iva_porcentaje"):
        op.drop_column("notas", "iva_porcentaje")
    if has_column("notas", "iva_incluido"):
        op.drop_column("notas", "iva_incluido")
