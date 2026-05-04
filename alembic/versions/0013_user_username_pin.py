"""move username + pin_hash from staff_members to users

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-04 19:30:00.000000

The PIN-login credential is identity-level (username + 6-digit PIN
that authenticates a *person*), not a per-shop staff record. Move
the columns to `users` so the same person can sign in across all
their staff roles. Login UI is gated to shop-side surfaces only —
PWA / customer side is connect-only.

Migrates existing values from staff_members → users via the user_id
join before dropping the staff columns.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS pin_hash VARCHAR")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username "
        "ON users(username) WHERE username IS NOT NULL"
    )

    # Best-effort migration: copy any existing values from
    # staff_members.username/pin_hash to the matching user. Subselect
    # filters to one row per user to dodge the unique constraint when
    # the same user happens to be staff at multiple shops with the
    # same username (shouldn't happen in practice but cheap guard).
    op.execute(
        """
        UPDATE users u
        SET username = sm.username,
            pin_hash = sm.pin_hash
        FROM (
            SELECT DISTINCT ON (user_id) user_id, username, pin_hash
            FROM staff_members
            WHERE username IS NOT NULL AND pin_hash IS NOT NULL
            ORDER BY user_id, invited_at
        ) sm
        WHERE u.id = sm.user_id
          AND u.username IS NULL
        """
    )

    # Drop the staff-level columns now that the data is on users.
    op.execute("DROP INDEX IF EXISTS ux_staff_members_shop_username")
    op.execute("DROP INDEX IF EXISTS ix_staff_members_username")
    op.execute("ALTER TABLE staff_members DROP COLUMN IF EXISTS pin_hash")
    op.execute("ALTER TABLE staff_members DROP COLUMN IF EXISTS username")


def downgrade() -> None:
    op.execute("ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS username VARCHAR")
    op.execute("ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS pin_hash VARCHAR")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_staff_members_username "
        "ON staff_members(username)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_staff_members_shop_username "
        "ON staff_members(shop_id, username) WHERE username IS NOT NULL"
    )
    op.execute("DROP INDEX IF EXISTS ux_users_username")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS pin_hash")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS username")
