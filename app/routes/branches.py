"""Branch CRUD — owner-only."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import SessionContext, get_current_shop, require_owner
from app.core.database import get_session
from app.models import Branch, Shop
from app.services.branch import create_branch, list_branches, update_branch

router = APIRouter()


@router.get("")
async def list_(
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
):
    return await list_branches(db, shop.id)


@router.post("")
async def create(
    name: str = Form(...),
    address: Optional[str] = Form(None),
    reward_mode: Optional[str] = Form(None),
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
):
    try:
        branch = await create_branch(db, shop, name, address, reward_mode)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return branch


@router.patch("/{branch_id}")
async def update(
    branch_id: UUID,
    name: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
):
    branch = await db.get(Branch, branch_id)
    if not branch or branch.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Branch not found")
    return await update_branch(db, branch, name=name, address=address)
