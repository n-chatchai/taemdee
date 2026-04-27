"""pluralize remaining single-word tables for naming consistency

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-27 09:00:00.000000

The codebase already used plural snake_case for multi-word tables
(credit_logs, topup_slips, deereach_campaigns, otp_codes, staff_members).
Single-word tables were left at SQLModel's default lowercase singular
(shop, customer, branch, point, redemption, offer, referral). This
migration pluralizes them so every table follows the same convention.

PostgreSQL preserves foreign key constraints across rename_table — FKs
reference target tables by OID, not by name — so this is non-destructive.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RENAMES = (
    ("shop", "shops"),
    ("customer", "customers"),
    ("branch", "branches"),
    ("point", "points"),
    ("redemption", "redemptions"),
    ("offer", "offers"),
    ("referral", "referrals"),
)


def upgrade() -> None:
    for old, new in _RENAMES:
        op.rename_table(old, new)


def downgrade() -> None:
    for old, new in _RENAMES:
        op.rename_table(new, old)
