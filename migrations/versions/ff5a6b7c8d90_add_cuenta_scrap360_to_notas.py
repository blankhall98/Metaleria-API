"""add cuenta_scrap360_id to notas

Revision ID: ff5a6b7c8d90
Revises: fe4f5a6b7c89
Create Date: 2026-04-23 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "ff5a6b7c8d90"
down_revision = "fe4f5a6b7c89"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector.get_columns(table))

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector.get_indexes(table))

    if not has_column("notas", "cuenta_scrap360_id"):
        op.add_column("notas", sa.Column("cuenta_scrap360_id", sa.Integer(), nullable=True))

    if not has_index("notas", "ix_notas_cuenta_scrap360_id"):
        op.create_index("ix_notas_cuenta_scrap360_id", "notas", ["cuenta_scrap360_id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector.get_columns(table))

    def has_index(table: str, index_name: str) -> bool:
        return any(idx["name"] == index_name for idx in inspector.get_indexes(table))

    if has_index("notas", "ix_notas_cuenta_scrap360_id"):
        op.drop_index("ix_notas_cuenta_scrap360_id", table_name="notas")

    if has_column("notas", "cuenta_scrap360_id"):
        op.drop_column("notas", "cuenta_scrap360_id")
