"""Team (staff) CRUD — owner-only. Renders S-staff list/add/invite + the
Staff.join landing page (which is NOT owner-gated since the staff is a
new visitor at that point)."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from app.core.templates import templates
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import SessionContext, get_current_shop, require_owner
from app.core.database import get_session
from app.models import Shop, StaffMember
from app.services.team import (
    list_staff,
    mint_invite_token,
    revoke_staff,
    update_permissions,
)

router = APIRouter()


@router.get("", response_class=HTMLResponse)
async def team_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
):
    """S-staff — list of pending + accepted staff for this shop."""
    members = await list_staff(db, shop.id)
    return templates.TemplateResponse(
        request=request,
        name="shop/team.html",
        context={"shop": shop, "members": members},
    )


@router.get("/add", response_class=HTMLResponse)
async def team_add_form(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(require_owner),
):
    """S-staff.add — single nickname field. POST creates the StaffMember
    record + mints an invite token, then redirects to /shop/team/{id}/invite."""
    return templates.TemplateResponse(
        request=request,
        name="shop/team_add.html",
        context={"shop": shop},
    )


@router.post("/add")
async def team_add_post(
    display_name: str = Form(""),
    can_void: Optional[str] = Form(None),
    can_deereach: Optional[str] = Form(None),
    can_topup: Optional[str] = Form(None),
    can_settings: Optional[str] = Form(None),
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
):
    name = (display_name or "").strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ใส่ชื่อเล่นพนักงานก่อนนะครับ")
    staff = StaffMember(
        shop_id=shop.id,
        display_name=name,
        can_void=bool(can_void),
        can_deereach=bool(can_deereach),
        can_topup=bool(can_topup),
        can_settings=bool(can_settings),
    )
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    await mint_invite_token(db, staff)
    return RedirectResponse(
        url=f"/shop/team/{staff.id}/invite",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/{staff_id}/invite", response_class=HTMLResponse)
async def team_invite_page(
    request: Request,
    staff_id: UUID,
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
):
    """S-staff.invite — QR + share link. Re-issues a fresh token if the
    current one is expired/missing so the owner always sees a valid QR."""
    staff = await db.get(StaffMember, staff_id)
    if not staff or staff.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบพนักงานนี้")

    from app.models.util import utcnow
    needs_token = (
        not staff.invite_token
        or not staff.invite_token_expires_at
        or staff.invite_token_expires_at < utcnow()
    )
    if needs_token:
        await mint_invite_token(db, staff)

    base_url = str(request.base_url).rstrip("/")
    join_url = f"{base_url}/staff/join?t={staff.invite_token}"

    import segno
    qr_svg = segno.make(join_url, error="m").svg_inline(
        scale=10, dark="#111111", light="#ffffff", border=1, omitsize=True
    )

    # Pretty-print the remaining TTL as HH:MM:SS for the design's
    # "หมดอายุใน 23:58:12" countdown line.
    remaining = staff.invite_token_expires_at - utcnow()
    total_seconds = max(int(remaining.total_seconds()), 0)
    hh, rem = divmod(total_seconds, 3600)
    mm, ss = divmod(rem, 60)
    expire_label = f"{hh:02d}:{mm:02d}:{ss:02d}"

    return templates.TemplateResponse(
        request=request,
        name="shop/team_invite.html",
        context={
            "shop": shop,
            "staff": staff,
            "join_url": join_url,
            "qr_svg": qr_svg,
            "expire_label": expire_label,
        },
    )


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
