"""drop customer_threads/messages, add inbox_replies

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-10 12:00:00.000000

Big-bang shift from a free-form customer ↔ shop chat to broadcast-
scoped replies, per design/taemdee-customer.html → inbox.message and
design/taemdee-shop.html → inbox.list / inbox.message.

  · Customer cannot initiate; replies only happen on the back of a
    DeeReach broadcast that landed in the customer's Inbox.
  · Shop side groups by campaign — `/shop/messages` lists broadcasts
    they sent and the customers who replied.
  · Free reply rule stays — credit cost lives on the broadcast itself,
    not on individual replies.

Drops customer_threads + customer_messages tables (pre-launch, so
no production data to migrate). Adds inbox_replies, parented on
inboxes.id with a denormalised shop_read_at marker so the shop list
can show an unread count per broadcast without scanning the table on
every render.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0022"
down_revision: Union[str, Sequence[str], None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the old chat tables in dependency order (messages → threads).
    op.execute("DROP TABLE IF EXISTS customer_messages CASCADE")
    op.execute("DROP TABLE IF EXISTS customer_threads CASCADE")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS inbox_replies (
            id UUID PRIMARY KEY,
            inbox_id UUID NOT NULL REFERENCES inboxes(id) ON DELETE CASCADE,
            sender VARCHAR NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (now() at time zone 'utc'),
            shop_read_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbox_replies_inbox_created "
        "ON inbox_replies(inbox_id, created_at)"
    )
    # Drives the unread chip on /shop/messages — count of customer-sender
    # replies whose shop_read_at IS NULL, scoped to the shop's inboxes.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbox_replies_shop_unread "
        "ON inbox_replies(inbox_id, sender) WHERE shop_read_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS inbox_replies")

    # Best-effort restore — recreates the old chat tables empty so a
    # rollback gets the schema back even though data was wiped.
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
