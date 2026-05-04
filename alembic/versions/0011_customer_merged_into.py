"""customers.merged_into_id — soft-delete on merge

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-04 18:30:00.000000

merge_users used to delete the source customer row when both source
and target had a customer profile. Stale cookies pointing at the
deleted row then 404'd, breaking the iOS PWA OAuth-handoff case
(callback runs in Safari, can't update PWA's cookie). Switch to a
soft-delete: keep the row, mark merged_into_id, and have
find_or_create_customer follow the chain transparently.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE customers "
        "ADD COLUMN IF NOT EXISTS merged_into_id UUID "
        "REFERENCES customers(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customers_merged_into_id "
        "ON customers(merged_into_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_customers_merged_into_id")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS merged_into_id")
