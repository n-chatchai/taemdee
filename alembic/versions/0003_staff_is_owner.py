"""staff_members.is_owner — owner is now a StaffMember row

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-04 03:00:00.000000

Adds is_owner so the owner can be modelled as a StaffMember with
is_owner=True. New shop signups create the owner row alongside the
Shop (auth callbacks); pre-existing shops get lazy-backfilled on first
post-deploy login. No data migration here — runtime handles it.

Idempotent for the same reason as 0002 — fresh DBs land in 0001's
metadata.create_all() with this column already in place.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE staff_members "
        "ADD COLUMN IF NOT EXISTS is_owner BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE staff_members DROP COLUMN IF EXISTS is_owner")
