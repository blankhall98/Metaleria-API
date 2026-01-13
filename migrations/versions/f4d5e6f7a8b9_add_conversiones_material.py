"""add conversiones de material

Revision ID: f4d5e6f7a8b9
Revises: f3c4d5e6f7a8
Create Date: 2026-01-13 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f4d5e6f7a8b9"
down_revision = "f3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversiones_material",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sucursal_id", sa.Integer(), sa.ForeignKey("sucursales.id"), nullable=False),
        sa.Column("material_origen_id", sa.Integer(), sa.ForeignKey("materiales.id"), nullable=False),
        sa.Column("cantidad_origen", sa.Numeric(12, 3), nullable=False),
        sa.Column("material_destino_id", sa.Integer(), sa.ForeignKey("materiales.id"), nullable=False),
        sa.Column("cantidad_destino", sa.Numeric(12, 3), nullable=False),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("comentario", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_conversiones_material_sucursal_id",
        "conversiones_material",
        ["sucursal_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversiones_material_origen_id",
        "conversiones_material",
        ["material_origen_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversiones_material_destino_id",
        "conversiones_material",
        ["material_destino_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversiones_material_usuario_id",
        "conversiones_material",
        ["usuario_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_conversiones_material_usuario_id", table_name="conversiones_material")
    op.drop_index("ix_conversiones_material_destino_id", table_name="conversiones_material")
    op.drop_index("ix_conversiones_material_origen_id", table_name="conversiones_material")
    op.drop_index("ix_conversiones_material_sucursal_id", table_name="conversiones_material")
    op.drop_table("conversiones_material")
