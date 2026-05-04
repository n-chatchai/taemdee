"""staff_members.picture_url for the staff-side avatar

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-04 02:30:00.000000

Adds a picture_url column to staff_members so a staff member's avatar
(rendered in s3_top.html) can be edited from the profile sheet —
mirrors customers.picture_url. NULL means "no custom picture", and
the avatar UI falls back to the display_name initial.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "staff_members",
        sa.Column("picture_url", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("staff_members", "picture_url")
