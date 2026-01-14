"""add corte caja movimientos y denominaciones

Revision ID: f8a9b0c1d2e3
Revises: f7a8b9c0d1e2
Create Date: 2026-01-14 04:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f8a9b0c1d2e3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_table(name: str) -> bool:
        return inspector.has_table(name)

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector.get_indexes(table))

    def has_column(table: str, column_name: str) -> bool:
        return any(col["name"] == column_name for col in inspector.get_columns(table))

    if has_table("cortes_caja") and not has_column("cortes_caja", "motivo_diferencia"):
        op.add_column("cortes_caja", sa.Column("motivo_diferencia", sa.String(length=255), nullable=True))

    if not has_table("corte_caja_movimientos"):
        op.create_table(
            "corte_caja_movimientos",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("corte_id", sa.Integer(), sa.ForeignKey("cortes_caja.id"), nullable=False),
            sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column(
                "tipo",
                sa.Enum("INGRESO", "EGRESO", "RETIRO", "DEPOSITO", name="corte_caja_movimiento_tipo"),
                nullable=False,
            ),
            sa.Column("descripcion", sa.String(length=255), nullable=False),
            sa.Column("monto", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    if not has_index("corte_caja_movimientos", "ix_corte_caja_movimientos_corte_id"):
        op.create_index("ix_corte_caja_movimientos_corte_id", "corte_caja_movimientos", ["corte_id"])
    if not has_index("corte_caja_movimientos", "ix_corte_caja_movimientos_usuario_id"):
        op.create_index("ix_corte_caja_movimientos_usuario_id", "corte_caja_movimientos", ["usuario_id"])
    if not has_index("corte_caja_movimientos", "ix_corte_caja_movimientos_tipo"):
        op.create_index("ix_corte_caja_movimientos_tipo", "corte_caja_movimientos", ["tipo"])

    if not has_table("corte_caja_denominaciones"):
        op.create_table(
            "corte_caja_denominaciones",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("corte_id", sa.Integer(), sa.ForeignKey("cortes_caja.id"), nullable=False),
            sa.Column("valor", sa.Numeric(12, 2), nullable=False),
            sa.Column("cantidad", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    if not has_index("corte_caja_denominaciones", "ix_corte_caja_denominaciones_corte_id"):
        op.create_index("ix_corte_caja_denominaciones_corte_id", "corte_caja_denominaciones", ["corte_id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_table(name: str) -> bool:
        return inspector.has_table(name)

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector.get_indexes(table))

    def has_column(table: str, column_name: str) -> bool:
        return any(col["name"] == column_name for col in inspector.get_columns(table))

    if has_table("corte_caja_denominaciones"):
        if has_index("corte_caja_denominaciones", "ix_corte_caja_denominaciones_corte_id"):
            op.drop_index("ix_corte_caja_denominaciones_corte_id", table_name="corte_caja_denominaciones")
        op.drop_table("corte_caja_denominaciones")

    if has_table("corte_caja_movimientos"):
        if has_index("corte_caja_movimientos", "ix_corte_caja_movimientos_tipo"):
            op.drop_index("ix_corte_caja_movimientos_tipo", table_name="corte_caja_movimientos")
        if has_index("corte_caja_movimientos", "ix_corte_caja_movimientos_usuario_id"):
            op.drop_index("ix_corte_caja_movimientos_usuario_id", table_name="corte_caja_movimientos")
        if has_index("corte_caja_movimientos", "ix_corte_caja_movimientos_corte_id"):
            op.drop_index("ix_corte_caja_movimientos_corte_id", table_name="corte_caja_movimientos")
        op.drop_table("corte_caja_movimientos")

    if has_table("cortes_caja") and has_column("cortes_caja", "motivo_diferencia"):
        op.drop_column("cortes_caja", "motivo_diferencia")
