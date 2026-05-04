"""pwa_token_pairings.originator_customer_id ON DELETE SET NULL

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-04 12:00:00.000000

merge_users deletes the source customer profile when both source and
target have one. If the source happened to be a Pairing originator,
Postgres blocked the delete with a FK violation. Switch the
constraint to ON DELETE SET NULL so the pair row's reference just
goes null — the connect flow already handles a null originator
(refuses with 400, telling the user to start over).
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = :t LIMIT 1"
    ), {"t": table}).first()
    return row is not None


def _constraint_name(table: str, column: str) -> str | None:
    bind = op.get_bind()
    row = bind.execute(text(
        "SELECT tc.constraint_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        "WHERE tc.table_name = :t "
        "  AND kcu.column_name = :c "
        "  AND tc.constraint_type = 'FOREIGN KEY' "
        "LIMIT 1"
    ), {"t": table, "c": column}).first()
    return row[0] if row else None


def upgrade() -> None:
    if not _table_exists("pwa_token_pairings"):
        return
    name = _constraint_name("pwa_token_pairings", "originator_customer_id")
    if name:
        op.execute(f'ALTER TABLE pwa_token_pairings DROP CONSTRAINT "{name}"')
    op.execute(
        "ALTER TABLE pwa_token_pairings "
        "ADD CONSTRAINT pwa_token_pairings_originator_customer_id_fkey "
        "FOREIGN KEY (originator_customer_id) REFERENCES customers(id) "
        "ON DELETE SET NULL"
    )


def downgrade() -> None:
    if not _table_exists("pwa_token_pairings"):
        return
    name = _constraint_name("pwa_token_pairings", "originator_customer_id")
    if name:
        op.execute(f'ALTER TABLE pwa_token_pairings DROP CONSTRAINT "{name}"')
    op.execute(
        "ALTER TABLE pwa_token_pairings "
        "ADD CONSTRAINT pwa_token_pairings_originator_customer_id_fkey "
        "FOREIGN KEY (originator_customer_id) REFERENCES customers(id)"
    )
