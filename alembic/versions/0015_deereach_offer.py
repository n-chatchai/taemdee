"""DeeReach attached-offer columns

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-06 13:00:00.000000

Adds three columns to deereach_campaigns so a campaign can carry an
optional "ของฝาก" (gift / discount coupon) alongside the message body:

    offer_label       — e.g. 'ลด 50 บาท ทุกเมนู'
    offer_image       — preset icon id (gift_box, coffee_cup, ...)
    offer_expires_at  — null = no expiry

The offer is rendered as a styled card inside the customer's inbox
message; staff verifies use manually at the shop. No separate
Voucher / Redemption entity is created — keeping v1 minimal.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE deereach_campaigns "
        "ADD COLUMN IF NOT EXISTS offer_label VARCHAR"
    )
    op.execute(
        "ALTER TABLE deereach_campaigns "
        "ADD COLUMN IF NOT EXISTS offer_image VARCHAR"
    )
    op.execute(
        "ALTER TABLE deereach_campaigns "
        "ADD COLUMN IF NOT EXISTS offer_expires_at TIMESTAMP WITHOUT TIME ZONE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE deereach_campaigns DROP COLUMN IF EXISTS offer_expires_at")
    op.execute("ALTER TABLE deereach_campaigns DROP COLUMN IF EXISTS offer_image")
    op.execute("ALTER TABLE deereach_campaigns DROP COLUMN IF EXISTS offer_label")
