"""staff_members.user_id nullable + display_name_hint for open invites

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-08 12:00:00.000000

Refactor: an "open seat" invite no longer pre-binds a User row. The
owner generates a token + permissions; the staff signs in with their
real LINE/Google/Facebook/phone identity and the token claim wires
the resolved User to the StaffMember row at login time. This is
schema-side support:

    user_id            — was NOT NULL, now nullable. NULL = unclaimed
                         pending invite.
    display_name_hint  — a label the owner attaches to the invite
                         ("พนักงาน เช้า") for the staff_join page; falls
                         back to user.display_name once claimed.

Existing staff rows are untouched — they all have user_id set, so
dropping the NOT NULL constraint is non-destructive. The new column
is nullable for the same reason: live owner-staff rows leave it NULL
and read display_name through the user relationship as before.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0018"
down_revision: Union[str, Sequence[str], None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE staff_members ALTER COLUMN user_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE staff_members "
        "ADD COLUMN IF NOT EXISTS display_name_hint VARCHAR"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE staff_members DROP COLUMN IF EXISTS display_name_hint"
    )
    # Backfill any unclaimed invites with a placeholder user before
    # restoring NOT NULL — production rollback should ideally drop
    # such rows manually first; this keeps the schema reversible.
    op.execute(
        "DELETE FROM staff_members WHERE user_id IS NULL"
    )
    op.execute("ALTER TABLE staff_members ALTER COLUMN user_id SET NOT NULL")
