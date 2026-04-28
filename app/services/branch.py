"""Branch (multi-location) operations.

Reward mode rules (per PRD §6.I):
- Shops start with 0 branches (single-location by default, reward_mode='shared').
- When the 2nd branch is added, the owner must pick reward_mode ('shared' or 'separate').
- Once set, the mode is locked — switching requires data migration (v2).
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Branch, Shop
from app.models.util import utcnow

_WEEKDAYS_TH = (
    "วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี",
    "วันศุกร์", "วันเสาร์", "วันอาทิตย์",
)


async def s3_top_context(
    db: AsyncSession,
    shop: Shop,
    now: Optional[datetime] = None,
) -> dict:
    """Common context for the S3.* shared topbar partial — weekday string
    + branch count + active branch label (only when multi-branch). Used by
    every full-page S3 route (dashboard / issue / customers / insights) so
    the day-caption row + branch pill render consistently."""
    if now is None:
        now = utcnow()
    branch_row = (await db.exec(
        select(
            func.count().over().label("total"),
            Branch.name,
        )
        .where(Branch.shop_id == shop.id)
        .order_by(Branch.created_at)
        .limit(1)
    )).first()
    if branch_row is None:
        branches_count = 0
        branch_label = None
    else:
        branches_count = branch_row[0]
        branch_label = branch_row[1] if branches_count > 1 else None
    return {
        "weekday_th": _WEEKDAYS_TH[now.weekday()],
        "branches_count": branches_count,
        "branch_label": branch_label,
    }

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
