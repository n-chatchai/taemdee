"""Add is_pwa to customers

Revision ID: a1b2c3d4e5f7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-02 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('customers', sa.Column('is_pwa', sa.Boolean(), server_default='false'))


def downgrade() -> None:
    op.drop_column('customers', 'is_pwa')