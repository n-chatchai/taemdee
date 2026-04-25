"""Team (staff) CRUD — owner-only. Renders S11 page; mutations redirect back."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import SessionContext, get_current_shop, require_owner
from app.core.database import get_session
from app.models import Shop, StaffMember
from app.services.team import (
    invite_staff,
    list_staff,
    revoke_staff,
    update_permissions,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def team_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
):
    members = await list_staff(db, shop.id)
    return templates.TemplateResponse(
        request=request,
        name="shop/team.html",
        context={"shop": shop, "members": members},
    )


@router.post("")
async def invite(
    phone: Optional[str] = Form(None),
    line_id: Optional[str] = Form(None),
    display_name: Optional[str] = Form(None),
    can_void: bool = Form(True),
    can_deereach: bool = Form(False),
    can_topup: bool = Form(False),
    can_settings: bool = Form(False),
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
):
    try:
        await invite_staff(
            db, shop,
            phone=phone, line_id=line_id, display_name=display_name,
            can_void=can_void, can_deereach=can_deereach,
            can_topup=can_topup, can_settings=can_settings,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return RedirectResponse(url="/shop/team", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{staff_id}/permissions")
async def update_perms(
    staff_id: UUID,
    can_void: Optional[bool] = Form(None),
    can_deereach: Optional[bool] = Form(None),
    can_topup: Optional[bool] = Form(None),
    can_settings: Optional[bool] = Form(None),
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
):
    staff = await db.get(StaffMember, staff_id)
    if not staff or staff.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found")
    flags = {
        k: v for k, v in {
            "can_void": can_void,
            "can_deereach": can_deereach,
            "can_topup": can_topup,
            "can_settings": can_settings,
        }.items() if v is not None
    }
    await update_permissions(db, staff, **flags)
    return RedirectResponse(url="/shop/team", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{staff_id}/revoke")
async def revoke(
    staff_id: UUID,
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
):
    staff = await db.get(StaffMember, staff_id)
    if not staff or staff.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found")
    await revoke_staff(db, staff)
    return RedirectResponse(url="/shop/team", status_code=status.HTTP_303_SEE_OTHER)
