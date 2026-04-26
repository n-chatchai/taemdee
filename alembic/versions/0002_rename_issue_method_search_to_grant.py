"""rename shop.issue_method_search → shop.issue_method_grant

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-26 22:00:00.000000

The column was renamed in code to match the design's "ให้แต้มลูกค้า" (grant)
intent and avoid collision with Point.issuance_method='system'. This is a
non-destructive RENAME so the existing per-shop toggle values survive.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("shop", "issue_method_search", new_column_name="issue_method_grant")


def downgrade() -> None:
    op.alter_column("shop", "issue_method_grant", new_column_name="issue_method_search")
