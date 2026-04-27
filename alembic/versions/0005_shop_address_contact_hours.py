"""shop: split address + add shop_phone + opening_hours per S10 design

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-27 14:00:00.000000

The S10 settings redesign splits the single `Shop.location` (province only)
into province + district + address_detail, and adds `shop_phone` (public
contact, distinct from the owner login `phone`) and `opening_hours` (JSON
map of 7 weekday entries). All new columns are nullable so existing rows
keep working with empty contact/address details until the owner fills them
in via S10.location / S10.contact.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shops", sa.Column("district", sa.String(), nullable=True))
    op.add_column("shops", sa.Column("address_detail", sa.String(), nullable=True))
    op.add_column("shops", sa.Column("shop_phone", sa.String(), nullable=True))
    op.add_column(
        "shops",
        sa.Column("opening_hours", JSONB().with_variant(sa.JSON(), "sqlite"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("shops", "opening_hours")
    op.drop_column("shops", "shop_phone")
    op.drop_column("shops", "address_detail")
    op.drop_column("shops", "district")
