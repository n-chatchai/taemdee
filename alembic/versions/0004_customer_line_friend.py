"""customers.line_friend_status / .line_messaging_blocked_at

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-04 04:00:00.000000

DeeReach's `line` channel previously counted any customer with a
non-NULL line_id as reachable, but a customer who hasn't added the
@taemdee OA as a friend can't actually receive a push (LINE returns
403). Two new columns let us gate the reachability filter:

  line_friend_status  : NULL | 'friended' | 'unfollowed'
  line_messaging_blocked_at : timestamp the unfollow / 403 was observed

Idempotent — fresh DBs get the columns from 0001's metadata.create_all.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS line_friend_status VARCHAR"
    )
    op.execute(
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS line_messaging_blocked_at TIMESTAMP"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS line_messaging_blocked_at")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS line_friend_status")
