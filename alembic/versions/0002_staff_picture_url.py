"""staff_members.picture_url for the staff-side avatar

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-04 02:30:00.000000

Adds a picture_url column to staff_members so a staff member's avatar
(rendered in s3_top.html) can be edited from the profile sheet —
mirrors customers.picture_url. NULL means "no custom picture", and
the avatar UI falls back to the display_name initial.

Idempotent: 0001 builds the schema from SQLModel metadata, which
already includes this column on a fresh DB. ADD COLUMN IF NOT EXISTS
keeps that path working without erroring; on an existing DB upgraded
from before the metadata change, the column is genuinely added.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE staff_members "
        "ADD COLUMN IF NOT EXISTS picture_url VARCHAR"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE staff_members DROP COLUMN IF EXISTS picture_url")
