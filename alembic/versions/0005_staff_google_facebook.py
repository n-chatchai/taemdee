"""staff_members.google_id / .facebook_id

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-04 05:00:00.000000

Adds Google + Facebook provider columns to StaffMember so the staff
profile sheet can show all four providers (LINE / Google / Facebook /
phone) in a single connect-status section, matching the customer side.

The shop-side Google + Facebook OAuth callbacks don't yet do the
StaffMember-first resolution that LINE + phone do — for now these
columns are read-only display surfaces; binding flows come next.

Idempotent — fresh DBs get the columns from 0001's metadata.create_all.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS google_id VARCHAR"
    )
    op.execute(
        "ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS facebook_id VARCHAR"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_staff_members_google_id "
        "ON staff_members (google_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_staff_members_facebook_id "
        "ON staff_members (facebook_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_staff_members_facebook_id")
    op.execute("DROP INDEX IF EXISTS ix_staff_members_google_id")
    op.execute("ALTER TABLE staff_members DROP COLUMN IF EXISTS facebook_id")
    op.execute("ALTER TABLE staff_members DROP COLUMN IF EXISTS google_id")
