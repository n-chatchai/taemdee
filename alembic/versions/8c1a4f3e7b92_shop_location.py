"""shop location

Revision ID: 8c1a4f3e7b92
Revises: 4153748364bb
Create Date: 2026-04-26 09:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8c1a4f3e7b92'
down_revision: Union[str, Sequence[str], None] = '4153748364bb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable `location` to shop (e.g., 'เชียงใหม่')."""
    op.add_column('shop', sa.Column('location', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('shop', 'location')
