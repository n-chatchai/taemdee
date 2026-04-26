"""shop issue_method_* toggles

Revision ID: d7b39e2f8a4c
Revises: c2e8a91d4f53
Create Date: 2026-04-26 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd7b39e2f8a4c'
down_revision: Union[str, Sequence[str], None] = 'c2e8a91d4f53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Per-shop manual-issuance toggles (S5 redesign 2026-04-26).

    Replaces the legacy single-value `issuance_method` enum. `customer_scan`
    is always implicitly on (every shop has a printable QR), so no column.
    """
    op.add_column(
        'shop',
        sa.Column(
            'issue_method_shop_scan',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        'shop',
        sa.Column(
            'issue_method_phone_entry',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        'shop',
        sa.Column(
            'issue_method_search',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('shop', 'issue_method_search')
    op.drop_column('shop', 'issue_method_phone_entry')
    op.drop_column('shop', 'issue_method_shop_scan')
