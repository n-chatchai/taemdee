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


def upgrade() -> None:
    SQLModel.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    SQLModel.metadata.drop_all(bind=op.get_bind())
