from datetime import timedelta

import pytest

from app.models import Point
from app.models.util import utcnow
from app.services.issuance import void_point
from app.services.redemption import (
    RedemptionError,
    active_point_count,
    redeem,
    void_redemption,
)


async def _seed_stamps(db, shop, customer, count: int) -> list[Point]:
    """Insert `count` stamps directly — bypasses the daily cap so tests can build
    a full card without waiting days."""
    stamps = []
    base = utcnow() - timedelta(hours=count)
    for i in range(count):
        s = Point(
            shop_id=shop.id,
            customer_id=customer.id,
            issuance_method="customer_scan",
            created_at=base + timedelta(minutes=i),
        )
        db.add(s)
        stamps.append(s)
    await db.commit()
    for s in stamps:
        await db.refresh(s)
    return stamps


async def test_active_count_initially_zero(db, shop, customer):
    assert await active_point_count(db, shop.id, customer.id) == 0


async def test_active_count_ignores_voided(db, shop, customer):
    stamps = await _seed_stamps(db, shop, customer, 3)
    await void_point(db, stamps[0])
    assert await active_point_count(db, shop.id, customer.id) == 2


async def test_redeem_happy_path(db, shop, customer):
    await _seed_stamps(db, shop, customer, 10)
    redemption = await redeem(db, shop, customer)
    assert redemption.shop_id == shop.id
    assert redemption.customer_id == customer.id
    assert redemption.is_voided is False
    # All 10 stamps should now have the redemption_id set
    assert await active_point_count(db, shop.id, customer.id) == 0


async def test_redeem_not_enough_raises(db, shop, customer):
    await _seed_stamps(db, shop, customer, 9)
    with pytest.raises(RedemptionError, match="Not enough stamps"):
        await redeem(db, shop, customer)


async def test_void_redemption_restores_stamps(db, shop, customer):
    await _seed_stamps(db, shop, customer, 10)
    redemption = await redeem(db, shop, customer)
    assert await active_point_count(db, shop.id, customer.id) == 0

    await void_redemption(db, redemption)
    # Stamps become available again — customer can re-redeem
    assert await active_point_count(db, shop.id, customer.id) == 10


async def test_separate_mode_requires_branch(db, shop, customer):
    shop.reward_mode = "separate"
    db.add(shop)
    await db.commit()

    with pytest.raises(RedemptionError, match="branch_id is required"):
        await redeem(db, shop, customer)
