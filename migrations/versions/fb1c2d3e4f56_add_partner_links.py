"""add explicit cliente/proveedor links

Revision ID: fb1c2d3e4f56
Revises: faa1b2c3d4e5
Create Date: 2026-02-05 16:22:27
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "fb1c2d3e4f56"
down_revision = "faa1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector.get_columns(table))

    def has_fk(table: str, fk_name: str) -> bool:
        return any(fk.get("name") == fk_name for fk in inspector.get_foreign_keys(table))

    def has_unique(table: str, uq_name: str) -> bool:
        return any(uq.get("name") == uq_name for uq in inspector.get_unique_constraints(table))

    with op.batch_alter_table("proveedores") as batch:
        if not has_column("proveedores", "linked_cliente_id"):
            batch.add_column(sa.Column("linked_cliente_id", sa.Integer(), nullable=True))
        if not has_fk("proveedores", "fk_proveedores_linked_cliente"):
            batch.create_foreign_key(
                "fk_proveedores_linked_cliente",
                "clientes",
                ["linked_cliente_id"],
                ["id"],
            )
        if not has_unique("proveedores", "uq_proveedores_linked_cliente_id"):
            batch.create_unique_constraint("uq_proveedores_linked_cliente_id", ["linked_cliente_id"])

    with op.batch_alter_table("clientes") as batch:
        if not has_column("clientes", "linked_proveedor_id"):
            batch.add_column(sa.Column("linked_proveedor_id", sa.Integer(), nullable=True))
        if not has_fk("clientes", "fk_clientes_linked_proveedor"):
            batch.create_foreign_key(
                "fk_clientes_linked_proveedor",
                "proveedores",
                ["linked_proveedor_id"],
                ["id"],
            )
        if not has_unique("clientes", "uq_clientes_linked_proveedor_id"):
            batch.create_unique_constraint("uq_clientes_linked_proveedor_id", ["linked_proveedor_id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_column(table: str, column: str) -> bool:
        return any(col["name"] == column for col in inspector.get_columns(table))

    def has_fk(table: str, fk_name: str) -> bool:
        return any(fk.get("name") == fk_name for fk in inspector.get_foreign_keys(table))

    def has_unique(table: str, uq_name: str) -> bool:
        return any(uq.get("name") == uq_name for uq in inspector.get_unique_constraints(table))

    with op.batch_alter_table("clientes") as batch:
        if has_unique("clientes", "uq_clientes_linked_proveedor_id"):
            batch.drop_constraint("uq_clientes_linked_proveedor_id", type_="unique")
        if has_fk("clientes", "fk_clientes_linked_proveedor"):
            batch.drop_constraint("fk_clientes_linked_proveedor", type_="foreignkey")
        if has_column("clientes", "linked_proveedor_id"):
            batch.drop_column("linked_proveedor_id")

    with op.batch_alter_table("proveedores") as batch:
        if has_unique("proveedores", "uq_proveedores_linked_cliente_id"):
            batch.drop_constraint("uq_proveedores_linked_cliente_id", type_="unique")
        if has_fk("proveedores", "fk_proveedores_linked_cliente"):
            batch.drop_constraint("fk_proveedores_linked_cliente", type_="foreignkey")
        if has_column("proveedores", "linked_cliente_id"):
            batch.drop_column("linked_cliente_id")
