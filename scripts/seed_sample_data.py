"""Seed sample points + redemptions for a shop, backdated 10 days.

Optional dev/staging seed — gated by SHOP_SAMPLE_ID (settings) or the
--shop-id CLI flag. When run, creates 30 anonymous "TaemDee Sample N"
customers and inserts up to 200 randomized points/day plus up to 10
redemptions/day for the past 10 days, so the dashboard / feed /
analytics surfaces have realistic activity to render.

Each redemption attaches the customer's earliest reward_threshold
points and stamps served_at = redemption time so the voucher reads
as "ใช้แล้ว".

Idempotent: if any sample-prefix user already has points at the
configured shop, the script exits early. Use --reset to delete every
"TaemDee Sample %" user globally (along with their points and
redemptions) before re-seeding.

Usage:
  uv run python scripts/seed_sample_data.py
  uv run python scripts/seed_sample_data.py --shop-id <UUID>
  uv run python scripts/seed_sample_data.py --reset
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

# Allow `python scripts/seed_sample_data.py` from the project root —
# without this, sys.path[0] is the scripts/ dir and `app` is not
# importable. Prepending the parent (project root) keeps the script
# runnable both standalone and as a module.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import delete, text, update
from sqlmodel import select

from app.core.config import settings
from app.core.database import SessionFactory, engine
from app.models import Customer, Point, Redemption, Shop, User


SAMPLE_NAME_PREFIX = "TaemDee Sample"
NUM_SAMPLE_CUSTOMERS = 30
DAYS_BACK = 10
POINTS_PER_DAY_MIN = 30
POINTS_PER_DAY_MAX = 200
REDEMPTIONS_PER_DAY_MAX = 10


def _resolve_shop_id(cli_value: str | None) -> UUID | None:
    raw = cli_value or settings.shop_sample_id
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


async def _wipe_existing_samples(db) -> int:
    """Delete every 'TaemDee Sample %' user globally along with their
    customers, points and redemptions. Returns the count of users
    removed (0 if nothing matched)."""
    sample_users = (await db.exec(
        select(User).where(User.display_name.like(f"{SAMPLE_NAME_PREFIX}%"))
    )).all()
    if not sample_users:
        return 0

    user_ids = [u.id for u in sample_users]
    customer_ids = [
        c.id for c in (await db.exec(
            select(Customer).where(Customer.user_id.in_(user_ids))
        )).all()
    ]

    if customer_ids:
        # Clear point→redemption FK before deleting redemptions so the
        # cascade order can't trip on the constraint.
        await db.exec(
            update(Point)
            .where(Point.customer_id.in_(customer_ids))
            .values(redemption_id=None)
        )
        await db.exec(
            delete(Redemption).where(Redemption.customer_id.in_(customer_ids))
        )
        await db.exec(
            delete(Point).where(Point.customer_id.in_(customer_ids))
        )
        await db.exec(
            delete(Customer).where(Customer.id.in_(customer_ids))
        )

    await db.exec(delete(User).where(User.id.in_(user_ids)))
    await db.commit()
    return len(user_ids)


async def _already_seeded(db, shop_id: UUID) -> bool:
    """True if any sample-prefix user already has points at this shop —
    cheap idempotency guard so re-running the script is a no-op.
    asyncpg won't auto-cast str→uuid, so the param is passed as a UUID
    object and the column comparison stays native."""
    row = (await db.exec(
        text(
            """
            SELECT 1
            FROM users u
            JOIN customers c ON c.user_id = u.id
            JOIN points p ON p.customer_id = c.id
            WHERE u.display_name LIKE :prefix
              AND p.shop_id = :sid
            LIMIT 1
            """
        ).bindparams(prefix=f"{SAMPLE_NAME_PREFIX}%", sid=shop_id)
    )).first()
    return row is not None


async def seed(shop_id: UUID, *, reset: bool = False) -> None:
    async with SessionFactory() as db:
        shop = await db.get(Shop, shop_id)
        if shop is None:
            print(f"shop not found: {shop_id}", file=sys.stderr)
            return
        reward_threshold = int(shop.reward_threshold or 10)

        if reset:
            removed = await _wipe_existing_samples(db)
            print(f"reset: removed {removed} sample user(s)")

        if await _already_seeded(db, shop_id):
            print("already seeded — pass --reset to re-seed")
            return

        rng = random.Random(int(shop_id))

        today_utc = datetime.now(timezone.utc).replace(microsecond=0)
        customer_anchor = today_utc - timedelta(days=DAYS_BACK + 1)

        customer_ids: list[UUID] = []
        for n in range(1, NUM_SAMPLE_CUSTOMERS + 1):
            user = User(
                display_name=f"{SAMPLE_NAME_PREFIX} {n}",
                created_at=customer_anchor,
            )
            db.add(user)
            await db.flush()
            customer = Customer(
                user_id=user.id,
                is_anonymous=True,
                created_at=customer_anchor,
            )
            db.add(customer)
            await db.flush()
            customer_ids.append(customer.id)
        await db.commit()

        total_points = 0
        total_redemptions = 0

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
                db.add(Point(
                    id=uuid4(),
                    shop_id=shop_id,
                    customer_id=cid,
                    issuance_method="system",
                    is_voided=False,
                    created_at=ts,
                ))
            await db.commit()
            total_points += n_points

            # Skip redemptions on the oldest day — let one day of
            # points accumulate first so there's something to redeem.
            if days_ago == DAYS_BACK:
                continue

            n_redemptions = rng.randint(0, REDEMPTIONS_PER_DAY_MAX)
            for _ in range(n_redemptions):
                redeem_ts = day.replace(
                    hour=rng.randint(10, 22),
                    minute=rng.randint(0, 59),
                    second=rng.randint(0, 59),
                )
                row = (await db.exec(
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
                    ).bindparams(
                        sid=shop_id,
                        redeem_ts=redeem_ts,
                        threshold=reward_threshold,
                    )
                )).first()
                if row is None:
                    continue
                cid = row[0]
                redemption = Redemption(
                    id=uuid4(),
                    shop_id=shop_id,
                    customer_id=cid,
                    is_voided=False,
                    served_at=redeem_ts,
                    created_at=redeem_ts,
                )
                db.add(redemption)
                await db.flush()
                # Attach the customer's earliest available points as
                # the ones consumed by this redemption.
                await db.exec(
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
                    ).bindparams(
                        rid=redemption.id,
                        sid=shop_id,
                        cid=cid,
                        redeem_ts=redeem_ts,
                    )
                )
                total_redemptions += 1
            await db.commit()

        print(
            f"seeded shop={shop_id}: {NUM_SAMPLE_CUSTOMERS} customers, "
            f"{total_points} points, {total_redemptions} redemptions "
            f"across {DAYS_BACK + 1} days"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shop-id",
        help="Shop UUID to seed against (overrides SHOP_SAMPLE_ID env)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing 'TaemDee Sample %%' users globally before re-seeding",
    )
    args = parser.parse_args()

    shop_id = _resolve_shop_id(args.shop_id)
    if shop_id is None:
        parser.error(
            "No shop id — set SHOP_SAMPLE_ID in .env or pass --shop-id <UUID>"
        )

    try:
        asyncio.run(seed(shop_id, reset=args.reset))
    finally:
        asyncio.run(engine.dispose())


if __name__ == "__main__":
    main()
