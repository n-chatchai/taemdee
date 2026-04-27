"""clear legacy display_name='Guest' rows so the welcome sheet reopens

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-27 11:00:00.000000

The Customer model used to default display_name to "Guest". Now we use
NULL = "haven't asked the welcome sheet yet"; skip stores a polite
"คุณลูกค้า" instead. Existing rows that still hold the literal "Guest"
should be reset to NULL so those guests get the welcome sheet on their
next visit (and stop being labelled "Guest" in the avatar / greeting).
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE customers SET display_name = NULL WHERE display_name = 'Guest'")


def downgrade() -> None:
    # Best-effort reverse — only re-applies the placeholder where it was
    # null AND the customer is still anonymous (the original conditions
    # for the old default).
    op.execute(
        "UPDATE customers SET display_name = 'Guest' "
        "WHERE display_name IS NULL AND is_anonymous = TRUE"
    )
