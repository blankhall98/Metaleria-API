"""Estatus de entrega por línea en la bitácora de llamadas (fase 2).

Un proveedor cierra varios viajes en una llamada y los entrega en días
distintos; marcar entregado cerraba la llamada completa. El estatus baja a la
línea (llamadas_proveedor_materiales) con su auditoría, y el de la cabecera
pasa a derivarse de las líneas.

Backfill: cada línea hereda el estatus (y la auditoría de entrega) de su
cabecera, para que el estado derivado coincida con el que ya se veía.

Revision ID: b3d5f7a9c1e2
Revises: a9b8c7d6e5f4
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b3d5f7a9c1e2"
down_revision = "a9b8c7d6e5f4"
branch_labels = None
depends_on = None

_ESTATUS_VALUES = ("PENDIENTE", "ENTREGADO", "NO_CONFIRMO")


def _estatus_type():
    # En Postgres el tipo llamada_proveedor_estatus ya existe (lo creó
    # c3d4e5f6a7b8 para la cabecera): create_type=False evita el CREATE TYPE
    # duplicado. En SQLite sa.Enum rinde VARCHAR + CHECK y no hay tipo global.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.ENUM(*_ESTATUS_VALUES, name="llamada_proveedor_estatus", create_type=False)
    return sa.Enum(*_ESTATUS_VALUES, name="llamada_proveedor_estatus")


def upgrade() -> None:
    with op.batch_alter_table("llamadas_proveedor_materiales") as batch:
        batch.add_column(
            sa.Column("estatus", _estatus_type(), nullable=False, server_default="PENDIENTE")
        )
        batch.add_column(sa.Column("entregada_at", sa.DateTime(), nullable=True))
        batch.add_column(
            sa.Column(
                "entregada_by_user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", name="fk_llamada_material_entregada_by"),
                nullable=True,
            )
        )
    op.create_index(
        "ix_llamadas_proveedor_materiales_estatus",
        "llamadas_proveedor_materiales",
        ["estatus"],
    )

    # Las líneas heredan el estatus y la auditoría de su cabecera (subconsulta
    # correlacionada: corre igual en SQLite y Postgres).
    op.execute(
        """
        UPDATE llamadas_proveedor_materiales
        SET estatus = (
            SELECT l.estatus FROM llamadas_proveedor l
            WHERE l.id = llamadas_proveedor_materiales.llamada_id
        )
        """
    )
    op.execute(
        """
        UPDATE llamadas_proveedor_materiales
        SET entregada_at = (
            SELECT l.entregada_at FROM llamadas_proveedor l
            WHERE l.id = llamadas_proveedor_materiales.llamada_id
        ),
        entregada_by_user_id = (
            SELECT l.entregada_by_user_id FROM llamadas_proveedor l
            WHERE l.id = llamadas_proveedor_materiales.llamada_id
        )
        WHERE estatus = 'ENTREGADO'
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llamadas_proveedor_materiales_estatus",
        table_name="llamadas_proveedor_materiales",
    )
    with op.batch_alter_table("llamadas_proveedor_materiales") as batch:
        batch.drop_column("entregada_by_user_id")
        batch.drop_column("entregada_at")
        batch.drop_column("estatus")
    # El tipo llamada_proveedor_estatus NO se toca: la cabecera lo sigue usando.
