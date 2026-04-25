"""Branch CRUD — owner-only. Renders S12 page; mutations redirect back."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from app.core.templates import templates
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import SessionContext, get_current_shop, require_owner
from app.core.database import get_session
from app.models import Branch, Shop
from app.services.branch import create_branch, list_branches, update_branch

router = APIRouter()


@router.get("", response_class=HTMLResponse)
async def branches_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
):
    branches = await list_branches(db, shop.id)
    return templates.TemplateResponse(
        request=request,
        name="shop/branches.html",
        context={"shop": shop, "branches": branches},
    )


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
        await create_branch(db, shop, name, address, reward_mode)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return RedirectResponse(url="/shop/branches", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{branch_id}/edit")
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
    await update_branch(db, branch, name=name, address=address)
    return RedirectResponse(url="/shop/branches", status_code=status.HTTP_303_SEE_OTHER)
