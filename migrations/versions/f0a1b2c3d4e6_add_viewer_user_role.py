"""add viewer user role

Revision ID: f0a1b2c3d4e6
Revises: e5f6a7b8c9d0
Create Date: 2026-04-01 14:30:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f0a1b2c3d4e6"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'visor'")


def downgrade() -> None:
    # PostgreSQL enums do not support removing values safely in-place.
    pass
