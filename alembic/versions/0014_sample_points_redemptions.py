"""sample points + redemptions backdated 10 days for SHOP_SAMPLE_ID

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-05 12:00:00.000000

Optional dev/staging seed data, gated by the env var SHOP_SAMPLE_ID.
When set to a real shop UUID, this migration:

  1. Creates 30 anonymous sample users + customers (display_name
     'TaemDee Sample N') so the dashboard has bodies to attribute
     activity to.
  2. Inserts up to 200 randomized points/day for the past 10 days,
     spread across the sample customers and across business hours.
  3. Inserts up to 10 redemptions/day starting from day-9 backwards
     (skips the oldest day so points already exist to redeem against).
     Each redemption consumes `shop.reward_threshold` of the
     customer's earliest available points and stamps `served_at` so
     the voucher reads as "ใช้แล้ว".

Idempotent: if any sample-prefix user already has points at the
configured shop, the upgrade is a no-op. Downgrade removes every
TaemDee Sample customer + their points + redemptions globally
(safe: prefix is namespaced).

If SHOP_SAMPLE_ID is unset (production / unconfigured environments),
both upgrade and downgrade are no-ops.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Sequence, Union
from uuid import UUID, uuid4

from alembic import op
from sqlalchemy import text

from app.core.config import settings


revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SAMPLE_NAME_PREFIX = "TaemDee Sample"
NUM_SAMPLE_CUSTOMERS = 30
DAYS_BACK = 10
POINTS_PER_DAY_MIN = 30
POINTS_PER_DAY_MAX = 200
REDEMPTIONS_PER_DAY_MAX = 10


def _shop_id() -> Union[UUID, None]:
    raw = settings.shop_sample_id
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def upgrade() -> None:
    shop_uuid = _shop_id()
    if shop_uuid is None:
        return

    bind = op.get_bind()

    shop_row = bind.execute(
        text("SELECT id, reward_threshold FROM shops WHERE id = :sid"),
        {"sid": str(shop_uuid)},
    ).first()
    if shop_row is None:
        return
    reward_threshold = int(shop_row[1] or 10)

    # Idempotency guard: if a sample-prefix user already has points at
    # this shop, treat the migration as already-applied. Lets re-runs
    # against the same DB stay no-op without flagging the migration as
    # broken.
    existing = bind.execute(
        text(
            """
            SELECT 1
            FROM users u
            JOIN customers c ON c.user_id = u.id
            JOIN points p ON p.customer_id = c.id
            WHERE u.display_name LIKE 'TaemDee Sample%'
              AND p.shop_id = :sid
            LIMIT 1
            """
        ),
        {"sid": str(shop_uuid)},
    ).first()
    if existing:
        return

    rng = random.Random(int(shop_uuid))

    today_utc = datetime.now(timezone.utc).replace(microsecond=0)
    customer_anchor = today_utc - timedelta(days=DAYS_BACK + 1)

    customer_ids: list[UUID] = []
    for n in range(1, NUM_SAMPLE_CUSTOMERS + 1):
        user_id = uuid4()
        customer_id = uuid4()
        display_name = f"{SAMPLE_NAME_PREFIX} {n}"
        bind.execute(
            text(
                """
                INSERT INTO users
                  (id, display_name, is_pwa, notifications_enabled, created_at)
                VALUES (:uid, :nm, false, true, :ts)
                """
            ),
            {"uid": str(user_id), "nm": display_name, "ts": customer_anchor},
        )
        bind.execute(
            text(
                """
                INSERT INTO customers (id, user_id, is_anonymous, created_at)
                VALUES (:cid, :uid, true, :ts)
                """
            ),
            {"cid": str(customer_id), "uid": str(user_id), "ts": customer_anchor},
        )
        customer_ids.append(customer_id)

    for days_ago in range(DAYS_BACK, -1, -1):
        day = today_utc - timedelta(days=days_ago)
        n_points = rng.randint(POINTS_PER_DAY_MIN, POINTS_PER_DAY_MAX)
        for _ in range(n_points):
            cid = rng.choice(customer_ids)
            ts = day.replace(
                hour=rng.randint(8, 21),
                minute=rng.randint(0, 59),
                second=rng.randint(0, 59),
            )
            bind.execute(
                text(
                    """
                    INSERT INTO points
                      (id, shop_id, customer_id, issuance_method, is_voided, created_at)
                    VALUES (:pid, :sid, :cid, 'system', false, :ts)
                    """
                ),
                {
                    "pid": str(uuid4()),
                    "sid": str(shop_uuid),
                    "cid": str(cid),
                    "ts": ts,
                },
            )

        # Skip redemptions on the oldest day — let one day of points
        # accumulate first so there's something to redeem against.
        if days_ago == DAYS_BACK:
            continue

        n_redemptions = rng.randint(0, REDEMPTIONS_PER_DAY_MAX)
        day_end = day.replace(hour=23, minute=59, second=59)
        for _ in range(n_redemptions):
            redeem_ts = day.replace(
                hour=rng.randint(10, 22),
                minute=rng.randint(0, 59),
                second=rng.randint(0, 59),
            )
            row = bind.execute(
                text(
                    """
                    SELECT customer_id
                    FROM points
                    WHERE shop_id = :sid
                      AND is_voided = false
                      AND redemption_id IS NULL
                      AND created_at <= :redeem_ts
                    GROUP BY customer_id
                    HAVING COUNT(*) >= :threshold
                    ORDER BY random()
                    LIMIT 1
                    """
                ),
                {
                    "sid": str(shop_uuid),
                    "redeem_ts": redeem_ts,
                    "threshold": reward_threshold,
                },
            ).first()
            if row is None:
                continue
            cid = row[0]
            rid = uuid4()
            bind.execute(
                text(
                    """
                    INSERT INTO redemptions
                      (id, shop_id, customer_id, is_voided, served_at, created_at)
                    VALUES (:rid, :sid, :cid, false, :served, :ts)
                    """
                ),
                {
                    "rid": str(rid),
                    "sid": str(shop_uuid),
                    "cid": str(cid),
                    "ts": redeem_ts,
                    "served": redeem_ts,
                },
            )
            bind.execute(
                text(
                    f"""
                    UPDATE points
                    SET redemption_id = :rid
                    WHERE id IN (
                      SELECT id FROM points
                      WHERE shop_id = :sid
                        AND customer_id = :cid
                        AND is_voided = false
                        AND redemption_id IS NULL
                        AND created_at <= :redeem_ts
                      ORDER BY created_at ASC
                      LIMIT {reward_threshold}
                    )
                    """
                ),
                {
                    "rid": str(rid),
                    "sid": str(shop_uuid),
                    "cid": str(cid),
                    "redeem_ts": redeem_ts,
                },
            )


def downgrade() -> None:
    shop_uuid = _shop_id()
    if shop_uuid is None:
        return

    bind = op.get_bind()

    rows = bind.execute(
        text(
            """
            SELECT c.id, u.id
            FROM users u
            JOIN customers c ON c.user_id = u.id
            WHERE u.display_name LIKE 'TaemDee Sample%'
            """
        )
    ).all()
    if not rows:
        return

    customer_ids = [str(r[0]) for r in rows]
    user_ids = [str(r[1]) for r in rows]

    # Order: clear FK references on points → drop redemptions → drop
    # points → drop customers → drop users.
    bind.execute(
        text(
            """
            UPDATE points SET redemption_id = NULL
            WHERE customer_id = ANY(:cids)
            """
        ),
        {"cids": customer_ids},
    )
    bind.execute(
        text("DELETE FROM redemptions WHERE customer_id = ANY(:cids)"),
        {"cids": customer_ids},
    )
    bind.execute(
        text("DELETE FROM points WHERE customer_id = ANY(:cids)"),
        {"cids": customer_ids},
    )
    bind.execute(
        text("DELETE FROM customers WHERE id = ANY(:cids)"),
        {"cids": customer_ids},
    )
    bind.execute(
        text("DELETE FROM users WHERE id = ANY(:uids)"),
        {"uids": user_ids},
    )
