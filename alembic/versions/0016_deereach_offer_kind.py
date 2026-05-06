"""DeeReach offer kind / amount / unit / starts_at

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-06 14:00:00.000000

Extends deereach_campaigns with the structured fields needed for the
"ลดราคา" (discount) offer variant alongside the existing "ของฟรี"
(free item) variant:

    offer_kind        — 'free_item' | 'discount' (NULL when no offer)
    offer_amount      — integer value for the discount variant
    offer_unit        — 'baht' | 'percent' (NULL outside discount)
    offer_starts_at   — start of validity range (existing
                        offer_expires_at is the end)

Customer-side display is still driven by the composed offer_label
text + offer_expires_at on the Inbox row — these new columns just
preserve the structured intent so v2 reports / cashier UX can read
"this owner sends 50-baht discounts" without re-parsing the label.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE deereach_campaigns "
        "ADD COLUMN IF NOT EXISTS offer_kind VARCHAR"
    )
    op.execute(
        "ALTER TABLE deereach_campaigns "
        "ADD COLUMN IF NOT EXISTS offer_amount INTEGER"
    )
    op.execute(
        "ALTER TABLE deereach_campaigns "
        "ADD COLUMN IF NOT EXISTS offer_unit VARCHAR"
    )
    op.execute(
        "ALTER TABLE deereach_campaigns "
        "ADD COLUMN IF NOT EXISTS offer_starts_at TIMESTAMP WITHOUT TIME ZONE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE deereach_campaigns DROP COLUMN IF EXISTS offer_starts_at")
    op.execute("ALTER TABLE deereach_campaigns DROP COLUMN IF EXISTS offer_unit")
    op.execute("ALTER TABLE deereach_campaigns DROP COLUMN IF EXISTS offer_amount")
    op.execute("ALTER TABLE deereach_campaigns DROP COLUMN IF EXISTS offer_kind")
