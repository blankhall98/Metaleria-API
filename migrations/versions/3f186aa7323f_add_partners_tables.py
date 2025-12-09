"""add partners tables

Revision ID: 3f186aa7323f
Revises: b8ce7443d1c3
Create Date: 2025-12-09 18:02:33.320516

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3f186aa7323f"
down_revision: Union[str, Sequence[str], None] = "b8ce7443d1c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: create proveedores and clientes tables."""
    op.create_table(
        "proveedores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre_completo", sa.String(length=200), nullable=False),
        sa.Column("telefono", sa.String(length=50), nullable=True),
        sa.Column("correo_electronico", sa.String(length=200), nullable=True),
        sa.Column("placas", sa.String(length=50), nullable=True),
        sa.Column(
            "activo",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.create_index(
        "ix_proveedores_nombre_completo",
        "proveedores",
        ["nombre_completo"],
    )
    op.create_index(
        "ix_proveedores_telefono",
        "proveedores",
        ["telefono"],
    )
    op.create_index(
        "ix_proveedores_correo_electronico",
        "proveedores",
        ["correo_electronico"],
    )
    op.create_index(
        "uq_proveedores_placas",
        "proveedores",
        ["placas"],
        unique=True,
    )

    op.create_table(
        "clientes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre_completo", sa.String(length=200), nullable=False),
        sa.Column("telefono", sa.String(length=50), nullable=True),
        sa.Column("correo_electronico", sa.String(length=200), nullable=True),
        sa.Column("placas", sa.String(length=50), nullable=True),
        sa.Column(
            "activo",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.create_index(
        "ix_clientes_nombre_completo",
        "clientes",
        ["nombre_completo"],
    )
    op.create_index(
        "ix_clientes_telefono",
        "clientes",
        ["telefono"],
    )
    op.create_index(
        "ix_clientes_correo_electronico",
        "clientes",
        ["correo_electronico"],
    )
    op.create_index(
        "uq_clientes_placas",
        "clientes",
        ["placas"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema: drop clientes and proveedores tables and indexes."""
    op.drop_index("uq_clientes_placas", table_name="clientes")
    op.drop_index("ix_clientes_correo_electronico", table_name="clientes")
    op.drop_index("ix_clientes_telefono", table_name="clientes")
    op.drop_index("ix_clientes_nombre_completo", table_name="clientes")
    op.drop_table("clientes")

    op.drop_index("uq_proveedores_placas", table_name="proveedores")
    op.drop_index("ix_proveedores_correo_electronico", table_name="proveedores")
    op.drop_index("ix_proveedores_telefono", table_name="proveedores")
    op.drop_index("ix_proveedores_nombre_completo", table_name="proveedores")
    op.drop_table("proveedores")
