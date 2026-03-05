"""add sucursal_id to proveedores and clientes

Revision ID: fc2d3e4f5a67
Revises: fb1c2d3e4f56
Create Date: 2026-03-05 16:20:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "fc2d3e4f5a67"
down_revision = "fb1c2d3e4f56"
branch_labels = None
depends_on = None


def _first_sucursal_id(bind) -> int | None:
    return bind.execute(sa.text("SELECT id FROM sucursales ORDER BY id LIMIT 1")).scalar()


def _infer_sucursal_id_by_name(bind, nombre_completo: str | None) -> int | None:
    if not nombre_completo:
        return None
    prefix = "Sucursal "
    if not nombre_completo.startswith(prefix):
        return None
    suc_name = nombre_completo.replace(prefix, "", 1).strip()
    if not suc_name:
        return None
    return bind.execute(
        sa.text("SELECT id FROM sucursales WHERE nombre = :nombre ORDER BY id LIMIT 1"),
        {"nombre": suc_name},
    ).scalar()


def upgrade():
    bind = op.get_bind()

    def inspector():
        return sa.inspect(bind)

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector().get_columns(table))

    def has_fk(table: str, fk_name: str) -> bool:
        return any(fk.get("name") == fk_name for fk in inspector().get_foreign_keys(table))

    def has_index(table: str, index_name: str) -> bool:
        return any(idx.get("name") == index_name for idx in inspector().get_indexes(table))

    if not has_column("proveedores", "sucursal_id"):
        with op.batch_alter_table("proveedores") as batch:
            batch.add_column(sa.Column("sucursal_id", sa.Integer(), nullable=True))

    if not has_column("clientes", "sucursal_id"):
        with op.batch_alter_table("clientes") as batch:
            batch.add_column(sa.Column("sucursal_id", sa.Integer(), nullable=True))

    if not has_index("proveedores", "ix_proveedores_sucursal_id"):
        op.create_index("ix_proveedores_sucursal_id", "proveedores", ["sucursal_id"])
    if not has_index("clientes", "ix_clientes_sucursal_id"):
        op.create_index("ix_clientes_sucursal_id", "clientes", ["sucursal_id"])

    if not has_fk("proveedores", "fk_proveedores_sucursal_id"):
        with op.batch_alter_table("proveedores") as batch:
            batch.create_foreign_key(
                "fk_proveedores_sucursal_id",
                "sucursales",
                ["sucursal_id"],
                ["id"],
            )
    if not has_fk("clientes", "fk_clientes_sucursal_id"):
        with op.batch_alter_table("clientes") as batch:
            batch.create_foreign_key(
                "fk_clientes_sucursal_id",
                "sucursales",
                ["sucursal_id"],
                ["id"],
            )

    default_sucursal_id = _first_sucursal_id(bind)
    if default_sucursal_id is None:
        raise RuntimeError("No existen sucursales para vincular clientes/proveedores.")

    proveedores_null = bind.execute(
        sa.text("SELECT id, nombre_completo FROM proveedores WHERE sucursal_id IS NULL")
    ).fetchall()
    for row in proveedores_null:
        inferred = _infer_sucursal_id_by_name(bind, row.nombre_completo)
        sid = inferred or default_sucursal_id
        bind.execute(
            sa.text("UPDATE proveedores SET sucursal_id = :sid WHERE id = :pid"),
            {"sid": sid, "pid": row.id},
        )

    clientes_null = bind.execute(
        sa.text("SELECT id, nombre_completo FROM clientes WHERE sucursal_id IS NULL")
    ).fetchall()
    for row in clientes_null:
        inferred = _infer_sucursal_id_by_name(bind, row.nombre_completo)
        sid = inferred or default_sucursal_id
        bind.execute(
            sa.text("UPDATE clientes SET sucursal_id = :sid WHERE id = :cid"),
            {"sid": sid, "cid": row.id},
        )

    with op.batch_alter_table("proveedores") as batch:
        batch.alter_column("sucursal_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("clientes") as batch:
        batch.alter_column("sucursal_id", existing_type=sa.Integer(), nullable=False)


def downgrade():
    bind = op.get_bind()

    def inspector():
        return sa.inspect(bind)

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector().get_columns(table))

    def has_fk(table: str, fk_name: str) -> bool:
        return any(fk.get("name") == fk_name for fk in inspector().get_foreign_keys(table))

    def has_index(table: str, index_name: str) -> bool:
        return any(idx.get("name") == index_name for idx in inspector().get_indexes(table))

    if has_index("clientes", "ix_clientes_sucursal_id"):
        op.drop_index("ix_clientes_sucursal_id", table_name="clientes")
    if has_column("clientes", "sucursal_id"):
        with op.batch_alter_table("clientes") as batch:
            if has_fk("clientes", "fk_clientes_sucursal_id"):
                batch.drop_constraint("fk_clientes_sucursal_id", type_="foreignkey")
            batch.drop_column("sucursal_id")

    if has_index("proveedores", "ix_proveedores_sucursal_id"):
        op.drop_index("ix_proveedores_sucursal_id", table_name="proveedores")
    if has_column("proveedores", "sucursal_id"):
        with op.batch_alter_table("proveedores") as batch:
            if has_fk("proveedores", "fk_proveedores_sucursal_id"):
                batch.drop_constraint("fk_proveedores_sucursal_id", type_="foreignkey")
            batch.drop_column("sucursal_id")
