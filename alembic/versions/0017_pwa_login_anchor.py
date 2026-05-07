"""pwa_login_anchors — pre-OAuth shop identity anchor for iOS PWA

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-07 18:00:00.000000

iOS standalone PWA installs (iOS 16.4+) have a cookie jar separate
from Safari, so the shop session cookie minted in the OAuth callback
(which lands in Safari) never reaches the PWA. This table mirrors how
the customers table acts as a stable identity for customer-side
OAuth: the PWA gets a `shop_pwa_anchor` cookie pointing at a row
here, the OAuth state JWT carries the anchor id, and the callback
updates the row with the resolved shop+staff. A POST /auth/pwa-claim
from the returning PWA mints the real session cookie.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0017"
down_revision: Union[str, Sequence[str], None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pwa_login_anchors (
            id UUID PRIMARY KEY,
            shop_id UUID REFERENCES shops(id),
            staff_id UUID REFERENCES staff_members(id),
            is_owner BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (now() at time zone 'utc'),
            claimed_at TIMESTAMP WITHOUT TIME ZONE,
            expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pwa_login_anchors_shop_id "
        "ON pwa_login_anchors(shop_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pwa_login_anchors_expires_at "
        "ON pwa_login_anchors(expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pwa_login_anchors")
