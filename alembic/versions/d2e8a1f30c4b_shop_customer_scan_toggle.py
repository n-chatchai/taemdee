"""Add issue_method_customer_scan to shops

Revision ID: d2e8a1f30c4b
Revises: cc14b080239d
Create Date: 2026-05-03 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd2e8a1f30c4b'
down_revision = 'cc14b080239d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'shops',
        sa.Column(
            'issue_method_customer_scan',
            sa.Boolean(),
            server_default=sa.text('true'),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('shops', 'issue_method_customer_scan')
