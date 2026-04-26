"""shop reward_image

Revision ID: 9f5e3b27c0a4
Revises: 8c1a4f3e7b92
Create Date: 2026-04-26 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9f5e3b27c0a4'
down_revision: Union[str, Sequence[str], None] = '8c1a4f3e7b92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable `reward_image` to shop.

    Default 'coffee_cup' for existing rows so they have a sensible icon
    until the owner re-runs S2.2.
    """
    op.add_column(
        'shop',
        sa.Column('reward_image', sa.String(), nullable=True, server_default='coffee_cup'),
    )


def downgrade() -> None:
    op.drop_column('shop', 'reward_image')
