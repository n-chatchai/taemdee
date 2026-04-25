import pytest

from app.services.branch import count_branches, create_branch, list_branches


async def test_count_empty(db, shop):
    assert await count_branches(db, shop.id) == 0


async def test_first_branch_no_mode_required(db, shop):
    branch = await create_branch(db, shop, name="Main")
    assert branch.name == "Main"
    assert shop.reward_mode == "shared"  # unchanged default


async def test_second_branch_requires_mode(db, shop):
    await create_branch(db, shop, name="Main")
    with pytest.raises(ValueError, match="reward_mode"):
        await create_branch(db, shop, name="Branch 2")


async def test_second_branch_sets_mode(db, shop):
    await create_branch(db, shop, name="Main")
    await create_branch(db, shop, name="Branch 2", reward_mode="separate")
    await db.refresh(shop)
    assert shop.reward_mode == "separate"


async def test_third_branch_ignores_mode_param(db, shop):
    await create_branch(db, shop, name="Main")
    await create_branch(db, shop, name="Branch 2", reward_mode="separate")
    # 3rd: passing reward_mode is silently ignored (locked)
    await create_branch(db, shop, name="Branch 3", reward_mode="shared")
    await db.refresh(shop)
    assert shop.reward_mode == "separate"


async def test_list_branches_ordered(db, shop):
    await create_branch(db, shop, name="First")
    await create_branch(db, shop, name="Second", reward_mode="shared")
    branches = await list_branches(db, shop.id)
    assert [b.name for b in branches] == ["First", "Second"]
