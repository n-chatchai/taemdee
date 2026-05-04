"""users table — canonical identity, columns moved off Customer + StaffMember

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-04 07:00:00.000000

Hard cut. Moves the four provider columns (line_id / google_id /
facebook_id / phone) plus display_name / picture_url / recovery_code /
line_friend_status / line_messaging_blocked_at / is_pwa / text_size
/ notifications_enabled / web_push_* off Customer and StaffMember
onto a new `users` table. Adds user_id FK on both sides.

Backfill order:
  1. customers: one user per existing row, copying identity + UX fields.
  2. staff_members: try to find an existing User by any of the four
     provider columns first (same person who's also a customer maps
     to ONE user). Otherwise create a new one.

After backfill the source columns are dropped from customers +
staff_members in this same migration. user_id is set NOT NULL.

Idempotent for fresh DBs built via SQLModel.metadata.create_all
(legacy columns aren't on the table to begin with — the column-exists
guard skips backfill cleanly).
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_MOVED_FROM_CUSTOMERS = (
    "line_id", "google_id", "facebook_id", "phone",
    "display_name", "picture_url",
    "recovery_code",
    "line_friend_status", "line_messaging_blocked_at",
    "is_pwa", "text_size", "notifications_enabled",
    "web_push_endpoint", "web_push_p256dh", "web_push_auth",
)
_MOVED_FROM_STAFF = (
    "line_id", "google_id", "facebook_id", "phone",
    "display_name", "picture_url",
)


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c LIMIT 1"
    ), {"t": table, "c": column}).first()
    return row is not None


def _constraint_exists(constraint_name: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(text(
        "SELECT 1 FROM information_schema.table_constraints "
        "WHERE constraint_name = :n LIMIT 1"
    ), {"n": constraint_name}).first()
    return row is not None


def upgrade() -> None:
    # ── 1. Create users table + indexes ────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id                          UUID PRIMARY KEY,
            line_id                     VARCHAR UNIQUE,
            google_id                   VARCHAR UNIQUE,
            facebook_id                 VARCHAR UNIQUE,
            phone                       VARCHAR UNIQUE,
            display_name                VARCHAR,
            picture_url                 VARCHAR,
            recovery_code               VARCHAR UNIQUE,
            line_friend_status          VARCHAR,
            line_messaging_blocked_at   TIMESTAMP,
            is_pwa                      BOOLEAN NOT NULL DEFAULT FALSE,
            text_size                   VARCHAR,
            notifications_enabled       BOOLEAN NOT NULL DEFAULT TRUE,
            web_push_endpoint           VARCHAR,
            web_push_p256dh             VARCHAR,
            web_push_auth               VARCHAR,
            created_at                  TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    for col in ("line_id", "google_id", "facebook_id", "phone", "recovery_code"):
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_users_{col} ON users ({col})"
        )

    # ── 2. Add user_id FK columns (nullable while backfill runs) ───────
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS user_id UUID")
    op.execute("ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS user_id UUID")

    # ── 3. Backfill users from customers ───────────────────────────────
    if _column_exists("customers", "line_id"):
        op.execute(
            """
            INSERT INTO users (
                id, line_id, google_id, facebook_id, phone,
                display_name, picture_url, recovery_code,
                line_friend_status, line_messaging_blocked_at,
                is_pwa, text_size, notifications_enabled,
                web_push_endpoint, web_push_p256dh, web_push_auth,
                created_at
            )
            SELECT
                gen_random_uuid(),
                line_id, google_id, facebook_id, phone,
                display_name, picture_url, recovery_code,
                line_friend_status, line_messaging_blocked_at,
                is_pwa, text_size, notifications_enabled,
                web_push_endpoint, web_push_p256dh, web_push_auth,
                created_at
            FROM customers
            WHERE user_id IS NULL
            """
        )
        # Bind each customer to the user we just created. Match by the
        # full identity tuple — created_at gives microsecond precision
        # so collisions among anonymous rows are vanishingly unlikely.
        op.execute(
            """
            UPDATE customers c
            SET user_id = u.id
            FROM users u
            WHERE c.user_id IS NULL
              AND u.created_at = c.created_at
              AND u.line_id     IS NOT DISTINCT FROM c.line_id
              AND u.google_id   IS NOT DISTINCT FROM c.google_id
              AND u.facebook_id IS NOT DISTINCT FROM c.facebook_id
              AND u.phone       IS NOT DISTINCT FROM c.phone
              AND u.recovery_code IS NOT DISTINCT FROM c.recovery_code
            """
        )

    # ── 4. Backfill staff_members → users ──────────────────────────────
    if _column_exists("staff_members", "line_id"):
        # Step 4a: link to existing user when the same provider id is
        # already on a user (i.e. the staff is also a customer with the
        # same LINE/Google/Facebook/phone).
        op.execute(
            """
            UPDATE staff_members s
            SET user_id = u.id
            FROM users u
            WHERE s.user_id IS NULL
              AND (
                  (s.line_id     IS NOT NULL AND u.line_id     = s.line_id)
               OR (s.google_id   IS NOT NULL AND u.google_id   = s.google_id)
               OR (s.facebook_id IS NOT NULL AND u.facebook_id = s.facebook_id)
               OR (s.phone       IS NOT NULL AND u.phone       = s.phone)
              )
            """
        )
        # Step 4b: anyone left over needs a fresh user.
        op.execute(
            """
            INSERT INTO users (
                id, line_id, google_id, facebook_id, phone,
                display_name, picture_url, created_at
            )
            SELECT
                gen_random_uuid(),
                line_id, google_id, facebook_id, phone,
                display_name, picture_url, invited_at
            FROM staff_members
            WHERE user_id IS NULL
            """
        )
        op.execute(
            """
            UPDATE staff_members s
            SET user_id = u.id
            FROM users u
            WHERE s.user_id IS NULL
              AND u.created_at = s.invited_at
              AND u.line_id     IS NOT DISTINCT FROM s.line_id
              AND u.google_id   IS NOT DISTINCT FROM s.google_id
              AND u.facebook_id IS NOT DISTINCT FROM s.facebook_id
              AND u.phone       IS NOT DISTINCT FROM s.phone
            """
        )

    # ── 5. FK constraints + NOT NULL ───────────────────────────────────
    if not _constraint_exists("fk_customers_user_id"):
        op.execute(
            "ALTER TABLE customers ADD CONSTRAINT fk_customers_user_id "
            "FOREIGN KEY (user_id) REFERENCES users(id)"
        )
    if not _constraint_exists("fk_staff_members_user_id"):
        op.execute(
            "ALTER TABLE staff_members ADD CONSTRAINT fk_staff_members_user_id "
            "FOREIGN KEY (user_id) REFERENCES users(id)"
        )
    op.execute("ALTER TABLE customers ALTER COLUMN user_id SET NOT NULL")
    op.execute("ALTER TABLE staff_members ALTER COLUMN user_id SET NOT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customers_user_id ON customers(user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_staff_members_user_id "
        "ON staff_members(user_id)"
    )

    # ── 6. Drop moved columns ──────────────────────────────────────────
    for col in _MOVED_FROM_CUSTOMERS:
        op.execute(f"ALTER TABLE customers DROP COLUMN IF EXISTS {col}")
    for col in _MOVED_FROM_STAFF:
        op.execute(f"ALTER TABLE staff_members DROP COLUMN IF EXISTS {col}")


def downgrade() -> None:
    # Best-effort. Re-running this from scratch requires a snapshot —
    # we don't keep enough info to reverse the user→customer/staff
    # split when more than one role shared a user.
    op.execute("ALTER TABLE customers DROP CONSTRAINT IF EXISTS fk_customers_user_id")
    op.execute("ALTER TABLE staff_members DROP CONSTRAINT IF EXISTS fk_staff_members_user_id")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS user_id")
    op.execute("ALTER TABLE staff_members DROP COLUMN IF EXISTS user_id")
    op.execute("DROP TABLE IF EXISTS users")
