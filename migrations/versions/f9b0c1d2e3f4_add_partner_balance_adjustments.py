"""add ajustes saldo partner

Revision ID: f9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-01-14 05:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f9b0c1d2e3f4"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_table(name: str) -> bool:
        return inspector.has_table(name)

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector.get_indexes(table))

    if not has_table("ajustes_saldo_partner"):
        op.create_table(
            "ajustes_saldo_partner",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("partner_type", sa.String(length=20), nullable=False),
            sa.Column("partner_id", sa.Integer(), nullable=False),
            sa.Column("monto", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("comentario", sa.String(length=255), nullable=True),
            sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    if not has_index("ajustes_saldo_partner", "ix_ajustes_saldo_partner_partner_type"):
        op.create_index(
            "ix_ajustes_saldo_partner_partner_type",
            "ajustes_saldo_partner",
            ["partner_type"],
        )
    if not has_index("ajustes_saldo_partner", "ix_ajustes_saldo_partner_partner_id"):
        op.create_index(
            "ix_ajustes_saldo_partner_partner_id",
            "ajustes_saldo_partner",
            ["partner_id"],
        )
    if not has_index("ajustes_saldo_partner", "ix_ajustes_saldo_partner_usuario_id"):
        op.create_index(
            "ix_ajustes_saldo_partner_usuario_id",
            "ajustes_saldo_partner",
            ["usuario_id"],
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_table(name: str) -> bool:
        return inspector.has_table(name)

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector.get_indexes(table))

    if has_table("ajustes_saldo_partner"):
        if has_index("ajustes_saldo_partner", "ix_ajustes_saldo_partner_usuario_id"):
            op.drop_index("ix_ajustes_saldo_partner_usuario_id", table_name="ajustes_saldo_partner")
        if has_index("ajustes_saldo_partner", "ix_ajustes_saldo_partner_partner_id"):
            op.drop_index("ix_ajustes_saldo_partner_partner_id", table_name="ajustes_saldo_partner")
        if has_index("ajustes_saldo_partner", "ix_ajustes_saldo_partner_partner_type"):
            op.drop_index("ix_ajustes_saldo_partner_partner_type", table_name="ajustes_saldo_partner")
        op.drop_table("ajustes_saldo_partner")
