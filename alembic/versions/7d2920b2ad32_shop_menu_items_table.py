"""shop_menu_items table

Revision ID: 7d2920b2ad32
Revises: 332396109c81
Create Date: 2026-05-01 17:28:35.152430

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '7d2920b2ad32'
down_revision: Union[str, Sequence[str], None] = '332396109c81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'shop_menu_items',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('shop_id', sa.Uuid(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('price', sa.Integer(), nullable=True),
        sa.Column('emoji', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('is_signature', sa.Boolean(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_shop_menu_items_shop_id'),
        'shop_menu_items',
        ['shop_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_shop_menu_items_shop_id'), table_name='shop_menu_items')
    op.drop_table('shop_menu_items')
