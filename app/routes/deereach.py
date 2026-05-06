"""DeeReach — S13 list / detail editor / sent confirmation + send action."""

from datetime import timedelta
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import SessionContext, get_current_shop, require_permission
from app.core.database import get_session
from app.core.templates import templates
from app.models import DeeReachCampaign, Point, Shop
from app.models.util import utcnow
from app.services.deereach import (
    CHANNEL_COST_SATANG,
    DeeReachSendError,
    Suggestion,
    _audience_for,
    _pick_channel,
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


# Static labels for the editor's app-bar / heading when compute_suggestions
# doesn't fire one (audience already messaged within 14d, customers
# graduated out of the eligibility window, manual kind, etc.). The editor
# still loads — 'no eligible recipients' is a less-frustrating outcome
# than 'ไม่มีแคมเปญแนะนำชนิดนี้' 404.
KIND_FALLBACK_LABELS: dict[str, tuple[str, str, str]] = {
    "win_back": ("ชวนกลับ", "ชวนคนที่หายไปกลับมา", "ไม่มีลูกค้าหายไปเข้าเงื่อนไขขณะนี้"),
    "almost_there": ("ใกล้ครบ", "ส่งกำลังใจคนใกล้ครบ", "ไม่มีลูกค้าใกล้รับรางวัลขณะนี้"),
    "unredeemed_reward": ("รับรางวัลซะที", "เตือนคนที่ยังไม่รับรางวัล", "ไม่มีคนค้างรับรางวัลขณะนี้"),
    "new_customer": ("ขอบคุณลูกค้าใหม่", "ขอบคุณคนที่มาครั้งแรก", "ไม่มีลูกค้าใหม่ในช่วง 7 วันที่ผ่านมา"),
    "manual": ("ข้อความของคุณเอง", "สร้างข้อความเอง", "เลือกลูกค้า + พิมพ์ข้อความเอง"),
}


@router.get("/{kind}", response_class=HTMLResponse)
async def deereach_detail(
    request: Request,
    kind: str,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """S13.detail — preview audience + default message before sending.
    Loads the live suggestion (has dynamic counts) when compute_suggestions
    fires for this kind, else falls back to a static-label Suggestion
    blob so the editor still opens with an empty 'no recipients' state
    instead of a 404 — happens when the eligible audience just got rate-
    limited or graduated out of the kind's window between the dashboard
    render and the tap."""
    if kind not in KIND_FALLBACK_LABELS:
        # Unknown kind in the URL (bookmark from an older route, typo'd
        # path, etc.) — bounce back to the DeeReach list rather than
        # showing a JSON 404 'ไม่รู้จักชนิดแคมเปญ' which is jarring.
        return RedirectResponse(
            url="/shop/deereach",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Manual is always a fallback (no live suggestion). For auto kinds,
    # try the live one first so the head/body lines reflect current
    # audience size; fall back to the static label otherwise.
    suggestion: Optional[Suggestion] = None
    if kind != "manual":
        suggestions = await compute_suggestions(db, shop)
        suggestion = next((s for s in suggestions if s.kind == kind), None)

    if suggestion is None:
        label, head, body = KIND_FALLBACK_LABELS[kind]
        suggestion = Suggestion(
            kind=kind, label=label, head=head, body=body,
            audience_count=0, cost_credit=0,
        )

    audience = await _audience_for(db, shop, kind)
    message = await render_message(kind, shop)

    # For the manual editor we expose segment chips above the
    # customer list so the owner can bulk-select "everyone almost
    # at threshold" / "everyone who lapsed" / "everyone who arrived
    # in the last 7d" without ticking each row individually. We
    # classify the manual audience inline rather than reusing the
    # find_*_customers helpers from the suggestion engine — those
    # require a LINE id (they're built for outreach), but the
    # manual audience is broader (anyone reachable, including
    # inbox-only customers). A single GROUP BY pulls the rollup,
    # then Python tags each customer based on activity pattern.
    audience_segments: dict[str, list[str]] = {}
    if kind == "manual" and audience:
        audience_id_set = {c.id for c in audience}
        point_rows = (await db.exec(
            select(Point.customer_id, Point.created_at, Point.redemption_id)
            .where(
                Point.shop_id == shop.id,
                Point.is_voided == False,  # noqa: E712
                Point.customer_id.in_(audience_id_set),
            )
        )).all()

        aggs: dict = {}
        for cid, created_at, redemption_id in point_rows:
            a = aggs.setdefault(cid, {"active": 0, "last_at": None, "first_at": None})
            if redemption_id is None:
                a["active"] += 1
            if a["last_at"] is None or created_at > a["last_at"]:
                a["last_at"] = created_at
            if a["first_at"] is None or created_at < a["first_at"]:
                a["first_at"] = created_at

        threshold = shop.reward_threshold or 1
        now_ts = utcnow()
        near_gap_max = 2
        lapsed_cutoff = now_ts - timedelta(days=14)
        new_cutoff = now_ts - timedelta(days=7)

        near_ids: list[str] = []
        lapsed_ids: list[str] = []
        new_ids: list[str] = []
        for cid in audience_id_set:
            agg = aggs.get(cid)
            if not agg:
                continue
            gap = threshold - agg["active"]
            if 0 < gap <= near_gap_max:
                near_ids.append(str(cid))
            if agg["last_at"] is not None and agg["last_at"] < lapsed_cutoff:
                lapsed_ids.append(str(cid))
            if agg["first_at"] is not None and agg["first_at"] >= new_cutoff:
                new_ids.append(str(cid))

        audience_segments = {
            "near":   near_ids,
            "lapsed": lapsed_ids,
            "new":    new_ids,
        }
    # Per-customer cost + channel matrix for the editor:
    #   audience_cost[id]      satang the campaign locks for this recipient
    #                          (drives the live ส่ง-button total)
    #   audience_channels[id]  { chosen, available[ch] } — drives the row
    #                          badge strip so the owner sees every channel
    #                          a customer has subscribed AND which one
    #                          waterfall will actually use (the chosen
    #                          badge is highlighted, others are dim).
    audience_cost: dict[str, int] = {}
    audience_channels: dict[str, dict] = {}
    for c in audience[:200]:
        chosen = _pick_channel(c)
        audience_cost[str(c.id)] = CHANNEL_COST_SATANG[chosen]
        audience_channels[str(c.id)] = {
            "chosen": chosen,
            "available": {
                "web_push": bool(c.web_push_endpoint),
                "line": bool(c.line_id),
                "sms": bool(c.phone),
                "inbox": True,  # always reachable, no subscription needed
            },
        }
    from app.services.deereach import DEEREACH_CHANNELS
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
            "audience_cost": audience_cost,
            "audience_channels": audience_channels,
            "audience_segments": audience_segments,
            "channels": DEEREACH_CHANNELS,
            "credit_balance_satang": shop.credit_balance,
        },
    )


@router.post("/send")
async def send(
    kind: str = Form(...),
    message: Optional[str] = Form(None),
    customer_ids: Optional[List[str]] = Form(None),
    offer_kind: Optional[str] = Form(None),
    offer_label: Optional[str] = Form(None),
    offer_image: Optional[str] = Form(None),
    offer_amount: Optional[int] = Form(None),
    offer_unit: Optional[str] = Form(None),
    offer_starts_at: Optional[str] = Form(None),
    offer_expires_at: Optional[str] = Form(None),
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
      - `offer_label / offer_image / offer_expires_at`: optional
        attached "ของฝาก" rendered alongside the message in inbox.
        offer_expires_at is a YYYY-MM-DD or ISO datetime string;
        empty/invalid → no expiry.
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

    # Parse optional date inputs — accept YYYY-MM-DD (the
    # <input type="date"> form value) and full ISO. Anything
    # unparseable becomes None rather than rejecting the whole send.
    from datetime import datetime as _dt

    def _parse_date(raw: Optional[str], end_of_day: bool = False):
        if not raw or not raw.strip():
            return None
        s = raw.strip()
        try:
            if "T" in s:
                return _dt.fromisoformat(s.replace("Z", ""))
            d = _dt.strptime(s, "%Y-%m-%d")
            if end_of_day:
                return d.replace(hour=23, minute=59, second=59)
            return d
        except ValueError:
            return None

    parsed_starts_at = _parse_date(offer_starts_at, end_of_day=False)
    parsed_expires_at = _parse_date(offer_expires_at, end_of_day=True)

    try:
        campaign = await send_campaign(
            db, shop, kind,
            message_override=message,
            selected_customer_ids=selected_set,
            offer_kind=offer_kind,
            offer_label=offer_label,
            offer_image=offer_image,
            offer_amount=offer_amount,
            offer_unit=offer_unit,
            offer_starts_at=parsed_starts_at,
            offer_expires_at=parsed_expires_at,
        )
    except DeeReachSendError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return RedirectResponse(
        url=f"/shop/deereach/sent?campaign_id={campaign.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
