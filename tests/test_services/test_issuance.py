import pytest

from app.services.branch import create_branch
from app.services.issuance import IssuanceError, issue_stamp, void_stamp


async def test_issue_creates_stamp(db, shop, customer):
    stamp = await issue_stamp(db, shop, customer, method="customer_scan")
    assert stamp.shop_id == shop.id
    assert stamp.customer_id == customer.id
    assert stamp.issuance_method == "customer_scan"
    assert stamp.is_voided is False


async def test_daily_cap_enforced(db, shop, customer):
    await issue_stamp(db, shop, customer, method="customer_scan")
    with pytest.raises(IssuanceError, match="Daily cap"):
        await issue_stamp(db, shop, customer, method="customer_scan")


async def test_system_method_bypasses_cap(db, shop, customer):
    await issue_stamp(db, shop, customer, method="customer_scan")
    # System method is for bonus/birthday stamps — bypasses the cap
    bonus = await issue_stamp(db, shop, customer, method="system")
    assert bonus.issuance_method == "system"


async def test_invalid_method_raises(db, shop, customer):
    with pytest.raises(ValueError, match="Invalid issuance method"):
        await issue_stamp(db, shop, customer, method="telepathy")


async def test_separate_mode_requires_branch(db, shop, customer):
    await create_branch(db, shop, name="Main")
    await create_branch(db, shop, name="B2", reward_mode="separate")
    await db.refresh(shop)

    with pytest.raises(IssuanceError, match="branch_id is required"):
        await issue_stamp(db, shop, customer, method="customer_scan")


async def test_void_marks_stamp(db, shop, customer):
    stamp = await issue_stamp(db, shop, customer, method="customer_scan")
    voided = await void_stamp(db, stamp)
    assert voided.is_voided is True
    assert voided.voided_at is not None
