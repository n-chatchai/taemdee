"""rename stamp table to point

Revision ID: d8e7c2a91f50
Revises: d7b39e2f8a4c
Create Date: 2026-04-26 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd8e7c2a91f50'
down_revision: Union[str, Sequence[str], None] = 'd7b39e2f8a4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename `stamp` table → `point` to match the UI's "แต้ม" terminology.

    No FK target columns reference `stamp.id`, so this is a pure table rename.
    Indexes follow the table automatically; no extra renames needed for them.
    """
    op.rename_table('stamp', 'point')


def downgrade() -> None:
    op.rename_table('point', 'stamp')
