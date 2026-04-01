"""add note balance adjustments

Revision ID: a1b2c3d4e5f7
Revises: f0a1b2c3d4e6
Create Date: 2026-04-01 16:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f7"
down_revision = "f0a1b2c3d4e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nota_ajustes_saldo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nota_id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.Column("monto_delta", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("saldo_anterior", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("saldo_resultante", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("comentario", sa.String(length=255), nullable=True),
        sa.Column("reversal_of_id", sa.Integer(), nullable=True),
        sa.Column("reverted_at", sa.DateTime(), nullable=True),
        sa.Column("reverted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("comentario_reversion", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["nota_id"], ["notas.id"]),
        sa.ForeignKeyConstraint(["reversal_of_id"], ["nota_ajustes_saldo.id"]),
        sa.ForeignKeyConstraint(["reverted_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["usuario_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_nota_ajustes_saldo_id"), "nota_ajustes_saldo", ["id"], unique=False)
    op.create_index(op.f("ix_nota_ajustes_saldo_nota_id"), "nota_ajustes_saldo", ["nota_id"], unique=False)
    op.create_index(op.f("ix_nota_ajustes_saldo_reversal_of_id"), "nota_ajustes_saldo", ["reversal_of_id"], unique=False)
    op.create_index(op.f("ix_nota_ajustes_saldo_reverted_by_user_id"), "nota_ajustes_saldo", ["reverted_by_user_id"], unique=False)
    op.create_index(op.f("ix_nota_ajustes_saldo_usuario_id"), "nota_ajustes_saldo", ["usuario_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_nota_ajustes_saldo_usuario_id"), table_name="nota_ajustes_saldo")
    op.drop_index(op.f("ix_nota_ajustes_saldo_reverted_by_user_id"), table_name="nota_ajustes_saldo")
    op.drop_index(op.f("ix_nota_ajustes_saldo_reversal_of_id"), table_name="nota_ajustes_saldo")
    op.drop_index(op.f("ix_nota_ajustes_saldo_nota_id"), table_name="nota_ajustes_saldo")
    op.drop_index(op.f("ix_nota_ajustes_saldo_id"), table_name="nota_ajustes_saldo")
    op.drop_table("nota_ajustes_saldo")
