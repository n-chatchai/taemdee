"""customer_items table — per-customer one-time-claimable dashboard todos

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-04 06:00:00.000000

Mirror of shop_items for the customer side. Surfaces a small todo
list on /my-cards (install PWA, link a social provider, friend
@taemdee on LINE, etc.). Row existence means "this customer has
already claimed or skipped this kind"; some kinds also auto-filter
out via an is_fulfilled() predicate in services/customer_items.py
based on live Customer state (e.g. customer.is_pwa).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_items (
            id           UUID PRIMARY KEY,
            customer_id  UUID NOT NULL REFERENCES customers (id),
            kind         VARCHAR NOT NULL,
            claimed_at   TIMESTAMP NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_customer_items_customer_kind UNIQUE (customer_id, kind)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customer_items_customer_id "
        "ON customer_items (customer_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_customer_items_customer_id")
    op.execute("DROP TABLE IF EXISTS customer_items")
