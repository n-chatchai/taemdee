"""shop scan_cooldown_minutes

Revision ID: c2e8a91d4f53
Revises: 9f5e3b27c0a4
Create Date: 2026-04-26 14:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2e8a91d4f53'
down_revision: Union[str, Sequence[str], None] = '9f5e3b27c0a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Per-shop rescan cooldown (minutes). 0 = no cooldown.

    Replaces the previous hardcoded "1 stamp / customer / day" rule so each
    shop can pick its own anti-abuse policy. Existing rows default to 0 (loose),
    matching the new code path.
    """
    op.add_column(
        'shop',
        sa.Column(
            'scan_cooldown_minutes',
            sa.Integer(),
            nullable=False,
            server_default='0',
        ),
    )


def downgrade() -> None:
    op.drop_column('shop', 'scan_cooldown_minutes')
