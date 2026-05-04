"""pwa_token_pairings.originator_customer_id

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-04 09:00:00.000000

Lets /auth/pair/start record the PWA's customer (decoded from the
customer cookie) so the OAuth callback in the system browser can bind
the new provider to the *same* User instead of forging a fresh one
that ends up cookie-overwriting the PWA's session on /redeem.
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = :t LIMIT 1"
    ), {"t": table}).first()
    return row is not None


def upgrade() -> None:
    # Fresh dev DBs created by SQLModel.metadata.create_all already have
    # the column (model picked it up); guard the ALTER so the migration
    # is a no-op there and only does real work on existing prod data.
    if not _table_exists("pwa_token_pairings"):
        return
    op.execute(
        "ALTER TABLE pwa_token_pairings "
        "ADD COLUMN IF NOT EXISTS originator_customer_id UUID "
        "REFERENCES customers(id)"
    )


def downgrade() -> None:
    if not _table_exists("pwa_token_pairings"):
        return
    op.execute(
        "ALTER TABLE pwa_token_pairings "
        "DROP COLUMN IF EXISTS originator_customer_id"
    )
