"""
Add nota_evidencias_extra table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1f4a9b2c3e4"
down_revision: Union[str, Sequence[str], None] = "c6f2b3a1d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("nota_evidencias_extra"):
        op.create_table(
            "nota_evidencias_extra",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nota_id", sa.Integer(), sa.ForeignKey("notas.id"), nullable=False),
            sa.Column("url", sa.String(length=255), nullable=False),
            sa.Column("uploaded_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    existing_idx = {idx["name"] for idx in inspector.get_indexes("nota_evidencias_extra")} if inspector.has_table("nota_evidencias_extra") else set()
    if "ix_nota_evidencias_extra_nota_id" not in existing_idx:
        op.create_index("ix_nota_evidencias_extra_nota_id", "nota_evidencias_extra", ["nota_id"])
    if "ix_nota_evidencias_extra_uploaded_by_id" not in existing_idx:
        op.create_index("ix_nota_evidencias_extra_uploaded_by_id", "nota_evidencias_extra", ["uploaded_by_id"])


def downgrade() -> None:
    op.drop_index("ix_nota_evidencias_extra_uploaded_by_id", table_name="nota_evidencias_extra")
    op.drop_index("ix_nota_evidencias_extra_nota_id", table_name="nota_evidencias_extra")
    op.drop_table("nota_evidencias_extra")
