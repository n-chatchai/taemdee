"""Staff.join — public landing page reached from the invite QR/link.

Auth-free (the visitor is a new staff member, no session yet). Renders
the shop name + LINE/phone login buttons. Login flows hand off to the
existing /auth/line and /auth/otp endpoints; the staff record is matched
on the post-login callback (TODO: wire token into the callback so we can
flip accepted_at + bind line_id/phone)."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from app.core.templates import templates
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.models import Shop
from app.services.team import find_pending_by_token

router = APIRouter()


@router.get("/staff/join", response_class=HTMLResponse)
async def staff_join_page(
    request: Request,
    t: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
):
    """Landing page for the staff invite. Shows shop name + nickname so
    the staff can confirm "this is me, I want to join", then offers
    LINE / phone login. Bad/expired token → friendly invite-expired
    state (still 200) so the staff knows to ask the owner for a fresh QR."""
    staff = await find_pending_by_token(db, t or "")
    shop = await db.get(Shop, staff.shop_id) if staff else None
    return templates.TemplateResponse(
        request=request,
        name="staff_join.html",
        context={
            "staff": staff,
            "shop": shop,
            "token": t or "",
        },
    )
