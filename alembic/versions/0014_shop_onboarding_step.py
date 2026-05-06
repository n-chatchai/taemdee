"""shops.onboarding_step — wizard cursor

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-06 12:00:00.000000

Adds a small int column that tracks the highest-numbered onboarding
step the shop owner has completed, so /shop/onboard can route a
returning owner back to where they left off (reward / theme) rather
than restarting at identity. Existing rows default to 0 (= identity);
already-onboarded shops continue to be routed to /shop/dashboard via
is_onboarded so the value doesn't matter for them in practice.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE shops "
        "ADD COLUMN IF NOT EXISTS onboarding_step INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE shops DROP COLUMN IF EXISTS onboarding_step")
