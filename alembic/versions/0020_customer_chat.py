"""customer_threads + customer_messages — customer ↔ shop chat

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-08 14:30:00.000000

Two-way chat between a Customer and a Shop. One thread per
(customer, shop) pair (UNIQUE) so opening a "new" message just
appends to the existing conversation. Messages are append-only
inside the thread; sender is "customer" or "shop". Unread counts
denormalized on the thread row to keep list views cheap.

Phase 1 = text only; attachment_url is nullable and unused until
Phase 3 wires up R2 uploads.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0020"
down_revision: Union[str, Sequence[str], None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_threads (
            id UUID PRIMARY KEY,
            customer_id UUID NOT NULL REFERENCES customers(id),
            shop_id UUID NOT NULL REFERENCES shops(id),
            last_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (now() at time zone 'utc'),
            customer_unread INTEGER NOT NULL DEFAULT 0,
            shop_unread INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (now() at time zone 'utc')
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_customer_threads_pair "
        "ON customer_threads(customer_id, shop_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customer_threads_shop_last "
        "ON customer_threads(shop_id, last_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customer_threads_customer_last "
        "ON customer_threads(customer_id, last_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_messages (
            id UUID PRIMARY KEY,
            thread_id UUID NOT NULL REFERENCES customer_threads(id),
            sender VARCHAR NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            attachment_url VARCHAR,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (now() at time zone 'utc'),
            read_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customer_messages_thread_created "
        "ON customer_messages(thread_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS customer_messages")
    op.execute("DROP TABLE IF EXISTS customer_threads")
