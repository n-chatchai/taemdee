"""customer google_id + facebook_id

Revision ID: b0ffdd3e82c1
Revises: 4fea385d541e
Create Date: 2026-05-01 12:57:39.189517

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'b0ffdd3e82c1'
down_revision: Union[str, Sequence[str], None] = '4fea385d541e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('customers', sa.Column('google_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('customers', sa.Column('facebook_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_index(op.f('ix_customers_google_id'), 'customers', ['google_id'], unique=True)
    op.create_index(op.f('ix_customers_facebook_id'), 'customers', ['facebook_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_customers_facebook_id'), table_name='customers')
    op.drop_index(op.f('ix_customers_google_id'), table_name='customers')
    op.drop_column('customers', 'facebook_id')
    op.drop_column('customers', 'google_id')
