"""add corte de caja

Revision ID: f7a8b9c0d1e2
Revises: f6a7b8c9d0e1
Create Date: 2026-01-14 03:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f7a8b9c0d1e2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_table(name: str) -> bool:
        return inspector.has_table(name)

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector.get_indexes(table))

    if not has_table("cortes_caja"):
        op.create_table(
            "cortes_caja",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("sucursal_id", sa.Integer(), sa.ForeignKey("sucursales.id"), nullable=False),
            sa.Column("abierto_por_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("cerrado_por_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("fecha", sa.Date(), nullable=False),
            sa.Column(
                "estado",
                sa.Enum("ABIERTO", "CERRADO", name="corte_caja_estado"),
                nullable=False,
            ),
            sa.Column("saldo_inicial", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("saldo_calculado", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("saldo_cierre", sa.Numeric(12, 2), nullable=True),
            sa.Column("diferencia", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("comentarios_cierre", sa.Text(), nullable=True),
            sa.Column("opened_at", sa.DateTime(), nullable=False),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("sucursal_id", "fecha", name="uq_corte_caja_sucursal_fecha"),
        )

    if not has_index("cortes_caja", "ix_cortes_caja_sucursal_id"):
        op.create_index("ix_cortes_caja_sucursal_id", "cortes_caja", ["sucursal_id"])
    if not has_index("cortes_caja", "ix_cortes_caja_fecha"):
        op.create_index("ix_cortes_caja_fecha", "cortes_caja", ["fecha"])
    if not has_index("cortes_caja", "ix_cortes_caja_estado"):
        op.create_index("ix_cortes_caja_estado", "cortes_caja", ["estado"])
    if not has_index("cortes_caja", "ix_cortes_caja_abierto_por_id"):
        op.create_index("ix_cortes_caja_abierto_por_id", "cortes_caja", ["abierto_por_id"])
    if not has_index("cortes_caja", "ix_cortes_caja_cerrado_por_id"):
        op.create_index("ix_cortes_caja_cerrado_por_id", "cortes_caja", ["cerrado_por_id"])

    if not has_table("corte_caja_gastos"):
        op.create_table(
            "corte_caja_gastos",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("corte_id", sa.Integer(), sa.ForeignKey("cortes_caja.id"), nullable=False),
            sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("descripcion", sa.String(length=255), nullable=False),
            sa.Column("categoria", sa.String(length=80), nullable=True),
            sa.Column("monto", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    if not has_index("corte_caja_gastos", "ix_corte_caja_gastos_corte_id"):
        op.create_index("ix_corte_caja_gastos_corte_id", "corte_caja_gastos", ["corte_id"])
    if not has_index("corte_caja_gastos", "ix_corte_caja_gastos_usuario_id"):
        op.create_index("ix_corte_caja_gastos_usuario_id", "corte_caja_gastos", ["usuario_id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_table(name: str) -> bool:
        return inspector.has_table(name)

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector.get_indexes(table))

    if has_table("corte_caja_gastos"):
        if has_index("corte_caja_gastos", "ix_corte_caja_gastos_usuario_id"):
            op.drop_index("ix_corte_caja_gastos_usuario_id", table_name="corte_caja_gastos")
        if has_index("corte_caja_gastos", "ix_corte_caja_gastos_corte_id"):
            op.drop_index("ix_corte_caja_gastos_corte_id", table_name="corte_caja_gastos")
        op.drop_table("corte_caja_gastos")

    if has_table("cortes_caja"):
        if has_index("cortes_caja", "ix_cortes_caja_cerrado_por_id"):
            op.drop_index("ix_cortes_caja_cerrado_por_id", table_name="cortes_caja")
        if has_index("cortes_caja", "ix_cortes_caja_abierto_por_id"):
            op.drop_index("ix_cortes_caja_abierto_por_id", table_name="cortes_caja")
        if has_index("cortes_caja", "ix_cortes_caja_estado"):
            op.drop_index("ix_cortes_caja_estado", table_name="cortes_caja")
        if has_index("cortes_caja", "ix_cortes_caja_fecha"):
            op.drop_index("ix_cortes_caja_fecha", table_name="cortes_caja")
        if has_index("cortes_caja", "ix_cortes_caja_sucursal_id"):
            op.drop_index("ix_cortes_caja_sucursal_id", table_name="cortes_caja")
        op.drop_table("cortes_caja")
