"""users.is_pwa → is_pwa_customer + is_pwa_shop split

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-08 13:30:00.000000

The single User.is_pwa flag conflated two unrelated facts:
  - "this user has installed the customer PWA" (drives the
    เพิ่มลงหน้าจอ todo + customer-side gift fulfillment)
  - "this user has installed the shop PWA" (drives the post-OAuth
    'go back to PWA' completion variant)

Persisting from both side-effects meant a shop owner who installed
shop.pwa but never customer.pwa got their customer todo silently
auto-fulfilled, and a desktop user with a prior phone-PWA install
got 'กลับไปเปิดแอป' on a desktop session. Split the column so each
signal is independent + queryable for usage stats.

Migration: rename is_pwa → is_pwa_customer (preserves the dominant
meaning — /track/pwa from c_base was its main writer), then add
is_pwa_shop default false. Existing rows keep whatever signal they
had as customer; shop-side stats start fresh from this point.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0019"
down_revision: Union[str, Sequence[str], None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE users RENAME COLUMN is_pwa TO is_pwa_customer")
    op.execute(
        "ALTER TABLE users "
        "ADD COLUMN IF NOT EXISTS is_pwa_shop BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_pwa_shop")
    op.execute("ALTER TABLE users RENAME COLUMN is_pwa_customer TO is_pwa")
