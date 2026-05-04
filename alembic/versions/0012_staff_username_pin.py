"""staff_members.username + pin_hash for username/PIN login

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-04 19:00:00.000000

Owner sets username + 6-digit PIN at staff creation; staff signs in
via /staff/pin-login at the shop subdomain. Username unique within
(shop_id) — composite partial index so different shops can each have
a "001". PIN is bcrypt-hashed.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS username VARCHAR")
    op.execute("ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS pin_hash VARCHAR")
    op.execute("CREATE INDEX IF NOT EXISTS ix_staff_members_username ON staff_members(username)")
    # Partial unique: only enforce when username is set, scoped per shop.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_staff_members_shop_username "
        "ON staff_members(shop_id, username) WHERE username IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_staff_members_shop_username")
    op.execute("DROP INDEX IF EXISTS ix_staff_members_username")
    op.execute("ALTER TABLE staff_members DROP COLUMN IF EXISTS pin_hash")
    op.execute("ALTER TABLE staff_members DROP COLUMN IF EXISTS username")
