"""customer last_link_prompt_snoozed_at

Revision ID: 332396109c81
Revises: b0ffdd3e82c1
Create Date: 2026-05-01 14:12:21.657710

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '332396109c81'
down_revision: Union[str, Sequence[str], None] = 'b0ffdd3e82c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'customers',
        sa.Column('last_link_prompt_snoozed_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('customers', 'last_link_prompt_snoozed_at')
