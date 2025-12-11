"""add logo to sucursales

Revision ID: c7a1b2c3d4e5
Revises: b1c2d3e4f5a6
Create Date: 2025-12-10 04:00:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c7a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sucursales", sa.Column("logo_url", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("sucursales", "logo_url")

