"""Add pairings (PWA OAuth handoff)

Revision ID: f4a91c2d3e6b
Revises: e3f0a9c1b552
Create Date: 2026-05-04 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f4a91c2d3e6b'
down_revision = 'e3f0a9c1b552'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pairings",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("pwa_token", sa.String(64), nullable=False),
        sa.Column(
            "customer_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_pairings_code", "pairings", ["code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_pairings_code", table_name="pairings")
    op.drop_table("pairings")
