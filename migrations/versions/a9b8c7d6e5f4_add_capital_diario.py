"""Capital contable diario: moneda en cuentas, comodín manual y fotos (punto 4, fase 2)

Revision ID: a9b8c7d6e5f4
Revises: d4e5f6a7b8c9
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a9b8c7d6e5f4"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cuentas_scrap360") as batch:
        batch.add_column(
            sa.Column("moneda", sa.String(length=3), nullable=False, server_default="MXN")
        )

    op.create_table(
        "capital_ajustes_manuales",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sucursal_id", sa.Integer(), sa.ForeignKey("sucursales.id"), nullable=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("monto", sa.Numeric(14, 2), nullable=False),
        sa.Column("concepto", sa.String(length=200), nullable=False),
        sa.Column("comentario", sa.String(length=255), nullable=True),
        sa.Column(
            "reversal_of_id",
            sa.Integer(),
            sa.ForeignKey("capital_ajustes_manuales.id"),
            nullable=True,
        ),
        sa.Column("reverted_at", sa.DateTime(), nullable=True),
        sa.Column(
            "reverted_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_capital_ajustes_manuales_sucursal_id", "capital_ajustes_manuales", ["sucursal_id"]
    )

    op.create_table(
        "capital_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("scope_key", sa.String(length=120), nullable=False),
        sa.Column("scope_label", sa.String(length=200), nullable=True),
        sa.Column("activos", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("pasivos", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("capital", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("tc_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column("detalle", sa.Text(), nullable=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("fecha", "scope_key", name="uq_capital_snapshots_fecha_scope"),
    )
    op.create_index("ix_capital_snapshots_fecha", "capital_snapshots", ["fecha"])
    op.create_index("ix_capital_snapshots_scope_key", "capital_snapshots", ["scope_key"])


def downgrade() -> None:
    op.drop_index("ix_capital_snapshots_scope_key", "capital_snapshots")
    op.drop_index("ix_capital_snapshots_fecha", "capital_snapshots")
    op.drop_table("capital_snapshots")
    op.drop_index("ix_capital_ajustes_manuales_sucursal_id", "capital_ajustes_manuales")
    op.drop_table("capital_ajustes_manuales")
    with op.batch_alter_table("cuentas_scrap360") as batch:
        batch.drop_column("moneda")
