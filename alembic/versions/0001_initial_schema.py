"""initial schema (snake_case + plural for multi-word tables)

Revision ID: 0001
Revises:
Create Date: 2026-04-26 21:00:00.000000

Fresh-start replacement for the prior 9-migration chain. All previous
migrations were dropped after a manual data wipe — this is the new
single source of truth.

Single-word tables keep SQLModel's lowercase default (shop, customer,
branch, point, redemption, offer, referral). Multi-word tables get
explicit plural snake_case names via __tablename__ on the model:
- credit_logs, topup_slips, deereach_campaigns, otp_codes, staff_members.
"""

from typing import Sequence, Union

from alembic import op
from sqlmodel import SQLModel

# Side-effect: registering every model class on SQLModel.metadata. Without this,
# create_all() below would issue a no-op since metadata would be empty.
from app import models  # noqa: F401


revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LEGACY_TABLES = (
    # Stale names from the previous, now-deleted migration chain.
    # Listed so a prod DB stamped at any earlier revision gets cleaned up.
    "stamp",            # → renamed to point
    "staffmember",      # → renamed to staff_members
    "creditlog",        # → renamed to credit_logs
    "topupslip",        # → renamed to topup_slips
    "deereachcampaign", # → renamed to deereach_campaigns
    "otpcode",          # → renamed to otp_codes
)


def upgrade() -> None:
    """Rebuilds the schema from scratch.

    Self-healing: drops any pre-existing tables (current names AND legacy
    singular names from the old migration chain) before recreating from the
    SQLModel metadata. This is intentional — coordinated with a data wipe.
    """
    bind = op.get_bind()
    # Drop legacy tables alembic doesn't know about anymore.
    for tname in _LEGACY_TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{tname}" CASCADE')
    # Drop current-name tables if they exist with stale columns
    SQLModel.metadata.drop_all(bind=bind)
    SQLModel.metadata.create_all(bind=bind)


def downgrade() -> None:
    SQLModel.metadata.drop_all(bind=op.get_bind())
