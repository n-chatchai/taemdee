"""deereach_events table — broadcast engagement event log

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-11 16:00:00.000000

Foundation table for the engagement track surface. One row per
recorded interaction (broadcast opened, customer replied, voucher
claimed, ...) so the stats page + per-customer engagement score
both query a homogeneous log instead of UNION-ing across Inbox /
InboxReply / Redemption.

Indexes mirror the three drill-down planes the stats page will run:
  · campaign × kind  — "how many opens / replies on this broadcast"
  · customer × kind  — engagement score per customer
  · shop × created_at — recent events feed
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS deereach_events (
            id UUID PRIMARY KEY,
            inbox_id UUID NOT NULL REFERENCES inboxes(id) ON DELETE CASCADE,
            customer_id UUID NOT NULL REFERENCES customers(id),
            shop_id UUID NOT NULL REFERENCES shops(id),
            campaign_id UUID REFERENCES deereach_campaigns(id),
            kind VARCHAR NOT NULL,
            payload TEXT,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
                DEFAULT (now() at time zone 'utc')
        )
        """
    )
    # Drives the campaign stats page — "events for this broadcast by
    # kind" → GROUP BY kind on the WHERE clause.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_deereach_events_campaign_kind "
        "ON deereach_events(campaign_id, kind)"
    )
    # Per-customer engagement score query — count events grouped by
    # kind for one customer, optionally bounded by created_at window.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_deereach_events_customer_kind "
        "ON deereach_events(customer_id, kind)"
    )
    # Recent-events feed on /shop/insights → newest-first scan of all
    # events for a shop.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_deereach_events_shop_created "
        "ON deereach_events(shop_id, created_at DESC)"
    )
    # Per-inbox lookup — used by the customer's broadcast detail page
    # to deduplicate "opened" events (don't log a second open within
    # the same session).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_deereach_events_inbox_kind "
        "ON deereach_events(inbox_id, kind)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS deereach_events")
