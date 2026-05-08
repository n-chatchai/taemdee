"""shops.allow_customer_messages — owner toggle for inbound chat

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-08 17:00:00.000000

Lets the owner gate the new customer→shop chat. Default TRUE so
existing shops keep working; an owner who'd rather not be DMed flips
it off in /shop/settings and the customer-side compose CTA on
shop.story disappears (and /messages/{shop_id} POST refuses).
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0021"
down_revision: Union[str, Sequence[str], None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE shops "
        "ADD COLUMN IF NOT EXISTS allow_customer_messages BOOLEAN NOT NULL DEFAULT TRUE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE shops DROP COLUMN IF EXISTS allow_customer_messages")
