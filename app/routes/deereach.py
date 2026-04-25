"""DeeReach send endpoint — owner / can_deereach staff taps to send a campaign."""

from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import SessionContext, get_current_shop, require_permission
from app.core.database import get_session
from app.models import Shop
from app.services.deereach import DeeReachSendError, send_campaign

router = APIRouter()


@router.post("/send")
async def send(
    kind: str = Form(...),
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(require_permission("can_deereach")),
    db: AsyncSession = Depends(get_session),
):
    try:
        await send_campaign(db, shop, kind)
    except DeeReachSendError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return RedirectResponse(url="/shop/dashboard", status_code=status.HTTP_303_SEE_OTHER)
