import pytest

from app.services.branch import create_branch
from app.services.issuance import IssuanceError, issue_point, void_point


async def test_issue_creates_stamp(db, shop, customer):
    stamp = await issue_point(db, shop, customer, method="customer_scan")
    assert stamp.shop_id == shop.id
    assert stamp.customer_id == customer.id
    assert stamp.issuance_method == "customer_scan"
    assert stamp.is_voided is False


async def test_zero_cooldown_allows_consecutive_scans(db, shop, customer):
    """Default config (scan_cooldown_minutes=0) should let the same customer
    re-scan as many times as they want — no anti-rescan protection."""
    s1 = await issue_point(db, shop, customer, method="customer_scan")
    s2 = await issue_point(db, shop, customer, method="customer_scan")
    assert s1.id != s2.id


async def test_cooldown_blocks_within_window(db, shop, customer):
    shop.scan_cooldown_minutes = 60
    db.add(shop)
    await db.commit()
    await db.refresh(shop)

    await issue_point(db, shop, customer, method="customer_scan")
    with pytest.raises(IssuanceError, match="cooldown"):
        await issue_point(db, shop, customer, method="customer_scan")


async def test_system_method_bypasses_cooldown(db, shop, customer):
    shop.scan_cooldown_minutes = 60
    db.add(shop)
    await db.commit()
    await db.refresh(shop)

    await issue_point(db, shop, customer, method="customer_scan")
    # `system` (bonus/birthday/admin) ignores the cooldown.
    bonus = await issue_point(db, shop, customer, method="system")
    assert bonus.issuance_method == "system"


async def test_invalid_method_raises(db, shop, customer):
    with pytest.raises(ValueError, match="Invalid issuance method"):
        await issue_point(db, shop, customer, method="telepathy")


async def test_separate_mode_requires_branch(db, shop, customer):
    await create_branch(db, shop, name="Main")
    await create_branch(db, shop, name="B2", reward_mode="separate")
    await db.refresh(shop)

    with pytest.raises(IssuanceError, match="branch_id is required"):
        await issue_point(db, shop, customer, method="customer_scan")


async def test_void_marks_stamp(db, shop, customer):
    stamp = await issue_point(db, shop, customer, method="customer_scan")
    voided = await void_point(db, stamp)
    assert voided.is_voided is True
    assert voided.voided_at is not None
