"""customer notifications_enabled

Revision ID: 295fe2727a2c
Revises: 7d2920b2ad32
Create Date: 2026-05-01 17:53:06.522560

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '295fe2727a2c'
down_revision: Union[str, Sequence[str], None] = '7d2920b2ad32'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'customers',
        sa.Column(
            'notifications_enabled',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('true'),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('customers', 'notifications_enabled')
