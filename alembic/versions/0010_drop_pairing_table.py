"""drop pwa_token_pairings table — pair flow removed

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-04 18:00:00.000000

The PWA OAuth pair flow (window.open → system browser → SSE → /redeem)
has been replaced with same-window navigation. /auth/{provider}/customer/start
now reads the customer cookie directly and bakes the customer id into
the OAuth state JWT. No more Pairing rows.

Drops the table cleanly on existing prod. Safe on fresh dev DBs
(IF EXISTS guard).
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pwa_token_pairings")
    # Some legacy installs still have the pre-rename name lying around.
    op.execute("DROP TABLE IF EXISTS pairings")


def downgrade() -> None:
    # No-op: re-creating the table from a removed model isn't useful.
    # If you really need to roll back, restore the model first and run
    # SQLModel.metadata.create_all on the bind.
    pass
