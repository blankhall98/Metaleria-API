"""add orden_display to materiales

Revision ID: fe4f5a6b7c89
Revises: fd3e4f5a6b78
Create Date: 2026-04-12 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "fe4f5a6b7c89"
down_revision = "fd3e4f5a6b78"
branch_labels = None
depends_on = None

# Canonical display order for metal materials
_CANONICAL_ORDER = [
    "Cobre 1",
    "Cobre 2",
    "Cobre 1 negro",
    "Bronce",
    "Radiador",
    "Radiador aluminio",
    "Bote",
    "Rvc",
    "Aluminio delgado",
    "Aluminio grueso",
    "Rin",
    "Perfil 1",
    "Perfil 2",
    "Cable aluminio",
    "Rebaba aluminio",
    "Litografía",
    "Bote spray",
    "Antimonio",
    "Acero 304",
    "Acero 201",
    "Rebaba cobre",
    "Rebaba bronce",
    "Estañado delgado",
    "Estañado grueso",
    "Radiador Plomo",
    "Cobre 2 especial",
    "Serpentín",
]


def upgrade():
    op.add_column(
        "materiales",
        sa.Column("orden_display", sa.Integer(), nullable=False, server_default="999"),
    )
    conn = op.get_bind()
    for idx, nombre in enumerate(_CANONICAL_ORDER, start=1):
        conn.execute(
            sa.text("UPDATE materiales SET orden_display = :orden WHERE nombre = :nombre"),
            {"orden": idx, "nombre": nombre},
        )


def downgrade():
    op.drop_column("materiales", "orden_display")
