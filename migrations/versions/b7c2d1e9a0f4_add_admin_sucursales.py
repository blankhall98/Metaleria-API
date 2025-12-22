"""add admin_sucursales table

Revision ID: b7c2d1e9a0f4
Revises: a3c9d1e7f2b0
Create Date: 2025-12-22 23:50:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c2d1e9a0f4"
down_revision: Union[str, Sequence[str], None] = "a3c9d1e7f2b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_sucursales",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("sucursal_id", sa.Integer(), sa.ForeignKey("sucursales.id"), primary_key=True),
    )
    op.create_index("ix_admin_sucursales_user_id", "admin_sucursales", ["user_id"])
    op.create_index("ix_admin_sucursales_sucursal_id", "admin_sucursales", ["sucursal_id"])
    op.execute(
        "INSERT INTO admin_sucursales (user_id, sucursal_id) "
        "SELECT id, sucursal_id FROM users "
        "WHERE rol = 'admin' AND sucursal_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_admin_sucursales_sucursal_id", table_name="admin_sucursales")
    op.drop_index("ix_admin_sucursales_user_id", table_name="admin_sucursales")
    op.drop_table("admin_sucursales")
