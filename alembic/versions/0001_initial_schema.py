"""initial schema (consolidated)

Revision ID: 0001
Revises:
Create Date: 2026-05-04 00:00:00.000000

Fresh-start replacement for the prior 26-migration chain, including the
rename pairings → pwa_token_pairings. All previous migrations were
dropped after a manual data wipe — this is the new single source of
truth.

Builds the schema directly from SQLModel.metadata so it always matches
the current models.
"""

from typing import Sequence, Union

from alembic import op
from sqlmodel import SQLModel

# Side-effect: registering every model class on SQLModel.metadata. Without this,
# create_all() below would issue a no-op since metadata would be empty.
from app import models  # noqa: F401


revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Build the schema from scratch.

    Drops then recreates from SQLModel metadata. Coordinated with a manual
    data wipe — not safe to run against a DB you want to keep.
    """
    bind = op.get_bind()
    SQLModel.metadata.drop_all(bind=bind)
    SQLModel.metadata.create_all(bind=bind)


def downgrade() -> None:
    SQLModel.metadata.drop_all(bind=op.get_bind())
