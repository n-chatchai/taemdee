"""inbox_replies.source — track where the reply came from

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-11 16:30:00.000000

Adds InboxReply.source so the shop-side thread can render a "ผ่าน LINE"
pill on replies that arrived via the @taemdee OA chat webhook,
distinguishing them from in-app replies POSTed to /my-inbox/<id>/reply.

Existing rows default to 'app' — they predate the LINE webhook mirror
(only path that wrote replies before this column existed).
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0024"
down_revision: Union[str, Sequence[str], None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE inbox_replies "
        "ADD COLUMN IF NOT EXISTS source VARCHAR NOT NULL DEFAULT 'app'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbox_replies_source "
        "ON inbox_replies(source)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_inbox_replies_source")
    op.execute("ALTER TABLE inbox_replies DROP COLUMN IF EXISTS source")
