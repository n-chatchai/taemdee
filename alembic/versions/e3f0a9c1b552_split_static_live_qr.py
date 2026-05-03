"""Split issue_method_customer_scan into static_qr + live_qr

Revision ID: e3f0a9c1b552
Revises: d2e8a1f30c4b
Create Date: 2026-05-03 21:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e3f0a9c1b552'
down_revision = 'd2e8a1f30c4b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The previous migration added issue_method_customer_scan as the
    # single toggle for the printed-QR path. Owners want finer control:
    # keep the rotating S3.qr live QR on while shutting off the static
    # counter sticker. Rename the existing column to its new role and
    # add a sibling for the live rotating QR (default on so existing
    # shops keep the same behavior).
    op.alter_column(
        'shops',
        'issue_method_customer_scan',
        new_column_name='issue_method_static_qr',
    )
    op.add_column(
        'shops',
        sa.Column(
            'issue_method_live_qr',
            sa.Boolean(),
            server_default=sa.text('true'),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('shops', 'issue_method_live_qr')
    op.alter_column(
        'shops',
        'issue_method_static_qr',
        new_column_name='issue_method_customer_scan',
    )
