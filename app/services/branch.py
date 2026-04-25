"""Branch (multi-location) operations.

Reward mode rules (per PRD §6.I):
- Shops start with 0 branches (single-location by default, reward_mode='shared').
- When the 2nd branch is added, the owner must pick reward_mode ('shared' or 'separate').
- Once set, the mode is locked — switching requires data migration (v2).
"""

from typing import List, Optional
from uuid import UUID

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Branch, Shop

VALID_REWARD_MODES = {"shared", "separate"}


async def count_branches(db: AsyncSession, shop_id: UUID) -> int:
    result = await db.exec(
        select(func.count()).select_from(Branch).where(Branch.shop_id == shop_id)
    )
    return result.one()


async def list_branches(db: AsyncSession, shop_id: UUID) -> List[Branch]:
    result = await db.exec(
        select(Branch).where(Branch.shop_id == shop_id).order_by(Branch.created_at)
    )
    return list(result.all())


async def create_branch(
    db: AsyncSession,
    shop: Shop,
    name: str,
    address: Optional[str] = None,
    reward_mode: Optional[str] = None,
) -> Branch:
    """Create a new branch.

    When adding the 2nd branch, `reward_mode` must be provided ('shared' | 'separate').
    For the 1st branch and 3rd+ branches, `reward_mode` is ignored (locked).
    """
    n = await count_branches(db, shop.id)

    if n == 1:
        if reward_mode not in VALID_REWARD_MODES:
            raise ValueError(
                "reward_mode is required when adding the 2nd branch "
                "(must be 'shared' or 'separate')"
            )
        shop.reward_mode = reward_mode
        db.add(shop)

    branch = Branch(shop_id=shop.id, name=name, address=address)
    db.add(branch)
    await db.commit()
    await db.refresh(branch)
    return branch


async def update_branch(
    db: AsyncSession,
    branch: Branch,
    name: Optional[str] = None,
    address: Optional[str] = None,
) -> Branch:
    if name is not None:
        branch.name = name
    if address is not None:
        branch.address = address
    db.add(branch)
    await db.commit()
    await db.refresh(branch)
    return branch
