"""DeeReach — S13 list / detail editor / sent confirmation + send action."""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import SessionContext, get_current_shop, require_permission
from app.core.database import get_session
from app.core.templates import templates
from app.models import DeeReachCampaign, Shop
from app.services.deereach import (
    DeeReachSendError,
    Suggestion,
    _audience_for,
    compute_suggestions,
    render_message,
    send_campaign,
)

router = APIRouter()


@router.get("", response_class=HTMLResponse)
async def deereach_list(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """S13 — list of system-recommended campaigns. Tapping a card opens
    /shop/deereach/{kind} (S13.detail) where the owner can preview and send."""
    suggestions = await compute_suggestions(db, shop)
    return templates.TemplateResponse(
        request=request,
        name="shop/deereach_list.html",
        context={"shop": shop, "suggestions": suggestions},
    )


@router.get("/sent", response_class=HTMLResponse)
async def deereach_sent_page(
    request: Request,
    campaign_id: UUID,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """S13.sent — confirmation after a successful send."""
    campaign = await db.get(DeeReachCampaign, campaign_id)
    if not campaign or campaign.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบแคมเปญที่ส่งล่าสุด")
    return templates.TemplateResponse(
        request=request,
        name="shop/deereach_sent.html",
        context={"shop": shop, "campaign": campaign},
    )


@router.get("/{kind}", response_class=HTMLResponse)
async def deereach_detail(
    request: Request,
    kind: str,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """S13.detail — preview audience + default message before sending. The
    audience checkboxes are read-only in v1 (send-to-all); per-customer
    deselect lands in v2 alongside richer audience filters."""
    suggestions = await compute_suggestions(db, shop)
    suggestion = next((s for s in suggestions if s.kind == kind), None)
    if not suggestion:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่มีแคมเปญแนะนำชนิดนี้สำหรับร้านคุณตอนนี้")

    audience = await _audience_for(db, shop, kind)
    message = await render_message(kind, shop)
    # Pass the full audience so the editor's checkboxes cover everyone —
    # the deselect UI can't work on a truncated list. Capped at 200 as a
    # sanity bound; campaigns with >200 recipients don't fit the per-row
    # UX anyway and would want a v2 segment-builder.
    return templates.TemplateResponse(
        request=request,
        name="shop/deereach_detail.html",
        context={
            "shop": shop,
            "suggestion": suggestion,
            "audience": audience[:200],
            "audience_total": len(audience),
            "message": message,
        },
    )


@router.post("/send")
async def send(
    kind: str = Form(...),
    message: Optional[str] = Form(None),
    customer_ids: Optional[List[str]] = Form(None),
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(require_permission("can_deereach")),
    db: AsyncSession = Depends(get_session),
):
    """Fire the send pipeline. On success → S13.sent confirmation. On any
    DeeReachSendError (no audience, blank message, no recipients selected,
    insufficient credits, …) → 400 with the informative Thai detail; the
    editor displays it as a flash.

    Optional form fields:
      - `message`: hand-edited body. Omit to use the per-kind default.
      - `customer_ids[]`: subset of the kind's eligible audience. Omit
        to send to everyone the audience query returns. Empty list (no
        checkboxes ticked) is treated the same as "no recipients" by
        the service.
    """
    selected_set: Optional[set[UUID]] = None
    if customer_ids is not None:
        selected_set = set()
        for cid in customer_ids:
            try:
                selected_set.add(UUID(cid))
            except ValueError:
                # Ignore malformed ids; service-side check still rejects an
                # empty selection so the user gets the right Thai detail.
                continue
    try:
        campaign = await send_campaign(
            db, shop, kind,
            message_override=message,
            selected_customer_ids=selected_set,
        )
    except DeeReachSendError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return RedirectResponse(
        url=f"/shop/deereach/sent?campaign_id={campaign.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
