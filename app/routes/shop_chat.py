"""Shop-side endpoints for the broadcast-scoped reply model.

Replaces the customer ↔ shop chat with replies parented on the shop's
DeeReach broadcasts (Inbox rows). The dock's "ข้อความ" entry shows
broadcasts the shop has sent + the customers who replied. Tapping a
reply opens the broadcast detail with the reply thread and a compose
box."""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import (
    SessionContext,
    get_current_shop,
    get_session_context,
)
from app.core.database import get_session
from app.core.templates import templates
from app.models import (
    Customer,
    DeeReachCampaign,
    DeeReachEvent,
    Inbox,
    InboxReply,
    Shop,
)
from app.services.branch import s3_top_context
from app.services.inbox_reply import (
    list_replies,
    mark_shop_read,
    send_reply,
)

router = APIRouter()


_DEEREACH_KIND_LABELS = {
    "win_back": "ชวนกลับ",
    "almost_there": "ใกล้ครบ",
    "unredeemed_reward": "เตือนรับรางวัล",
    "new_customer": "ขอบคุณลูกค้าใหม่",
    "re_engage": "กลับมาคุยกัน",
    "birthday": "อวยพรวันเกิด",
    "manual": "ข้อความสร้างเอง",
}


@router.get("/messages", response_class=HTMLResponse)
async def shop_messages_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_session),
):
    """Shop messages — DeeReach broadcasts the shop sent, grouped by
    campaign with the customers who replied listed under each. Per
    design/taemdee-shop.html → inbox.list. Replies are scoped per
    broadcast (no general chat); customers can't initiate."""
    # Every Inbox row this shop has sent — design wants the operator
    # to see broadcasts even before anyone replies, so they can verify
    # the send landed and pre-empt the conversation. Cap at 50 to keep
    # render snappy.
    inbox_rows = (await db.exec(
        select(Inbox)
        .where(Inbox.shop_id == shop.id)
        .order_by(Inbox.created_at.desc())
        .limit(50)
    )).all()

    customer_by_id: dict = {}
    if inbox_rows:
        cids = [r.customer_id for r in inbox_rows]
        rows = (await db.exec(
            select(Customer).where(Customer.id.in_(cids))
        )).all()
        customer_by_id = {c.id: c for c in rows}

    # Pull the latest reply per inbox for the row preview + sort key,
    # plus an unread count of customer-sender replies the shop hasn't
    # opened yet (drives the per-row chip).
    last_reply_by_inbox: dict = {}
    unread_by_inbox: dict = {}
    if inbox_rows:
        ibx_ids = [r.id for r in inbox_rows]
        replies = (await db.exec(
            select(InboxReply)
            .where(InboxReply.inbox_id.in_(ibx_ids))
            .order_by(InboxReply.inbox_id, InboxReply.created_at.desc())
        )).all()
        for rp in replies:
            last_reply_by_inbox.setdefault(rp.inbox_id, rp)
            if rp.sender == "customer" and rp.shop_read_at is None:
                unread_by_inbox[rp.inbox_id] = unread_by_inbox.get(rp.inbox_id, 0) + 1

    # Hydrate the campaigns the inboxes belong to so we can section
    # the list by "DeeReach · X · ส่งเมื่อ Y".
    campaign_by_id: dict = {}
    cids = {r.campaign_id for r in inbox_rows if r.campaign_id is not None}
    if cids:
        cps = (await db.exec(
            select(DeeReachCampaign).where(DeeReachCampaign.id.in_(cids))
        )).all()
        campaign_by_id = {cp.id: cp for cp in cps}

    # Compose row dicts in latest-reply-first order; the template
    # buckets them into sections by campaign_id.
    inbox_rows_sorted = sorted(
        inbox_rows,
        key=lambda r: (last_reply_by_inbox.get(r.id).created_at if last_reply_by_inbox.get(r.id) else r.created_at),
        reverse=True,
    )
    rows_view = []
    for r in inbox_rows_sorted:
        c = customer_by_id.get(r.customer_id)
        last = last_reply_by_inbox.get(r.id)
        if last is None:
            preview = ""
            sort_at = r.created_at
        else:
            prefix = "พี่: " if last.sender == "customer" else "คุณ: "
            preview = prefix + (last.body or "")
            sort_at = last.created_at
        rows_view.append({
            "inbox": r,
            "campaign": campaign_by_id.get(r.campaign_id) if r.campaign_id else None,
            "customer": c,
            "name": (c and c.display_name) or "ลูกค้า",
            "preview": preview,
            "sort_at": sort_at,
            "href": f"/shop/messages/{r.id}",
            "unread": unread_by_inbox.get(r.id, 0),
        })

    # Broadcasts — one .ib-broadcast card per campaign (the "ทั่วไป"
    # bucket holds inboxes with no campaign link, which only happens
    # for manual sends that didn't get a campaign row). Each broadcast
    # carries: headline (the body's first line — already placeholder-
    # expanded by the dispatcher; cp.message_text would still have
    # raw {name}/{shop_name}), the offer-label-or-kind tag for the
    # title suffix, sent_at, delivered/opened counts, and the flat
    # list of customer rows underneath.
    broadcasts: list = []
    seen: set = set()
    for v in rows_view:
        cp = v["campaign"]
        key = cp.id if cp else None
        if key in seen:
            continue
        seen.add(key)
        section_rows = [r for r in rows_view if (r["campaign"].id if r["campaign"] else None) == key]
        delivered = len(section_rows)
        opened = sum(1 for r in section_rows if r["inbox"].read_at is not None)
        if cp is not None:
            tag = (cp.offer_label or "").strip() or _DEEREACH_KIND_LABELS.get(cp.kind, "ส่งให้ลูกค้า")
            sample_inbox = section_rows[0]["inbox"] if section_rows else None
            sample_body = (sample_inbox.body if sample_inbox else (cp.message_text or "")) or ""
            headline = sample_body.strip().splitlines()[0] if sample_body else tag
            broadcasts.append({
                "campaign_id": cp.id,
                "headline": headline,
                "tag": tag,
                "sent_at": cp.sent_at or (sample_inbox.created_at if sample_inbox else None),
                "delivered": delivered,
                "opened": opened,
                "rows": section_rows,
            })
        else:
            sample_inbox = section_rows[0]["inbox"] if section_rows else None
            broadcasts.append({
                "campaign_id": None,
                "headline": "ทั่วไป",
                "tag": "ทั่วไป",
                "sent_at": sample_inbox.created_at if sample_inbox else None,
                "delivered": delivered,
                "opened": opened,
                "rows": section_rows,
            })

    # Page-head counts — total unread customer replies + total
    # broadcasts (sections). Surfaced in messages_list.html's page-head
    # sub line ("N ยังไม่ได้ตอบ · M broadcasts") per design.
    unread_total = sum(unread_by_inbox.values())
    broadcasts_total = len(broadcasts)

    _is_owner = bool(request.state.staff and request.state.staff.is_owner)
    s3_top = await s3_top_context(db, shop, is_owner=_is_owner)
    return templates.TemplateResponse(
        request=request,
        name="shop/messages_list.html",
        context={
            "shop": shop,
            "broadcasts": broadcasts,
            "unread_total": unread_total,
            "broadcasts_total": broadcasts_total,
            **s3_top,
        },
    )


@router.get("/messages/{inbox_id}", response_class=HTMLResponse)
async def shop_messages_thread(
    request: Request,
    inbox_id: UUID,
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_session),
):
    """Broadcast detail (shop side) — ix-deereach card + delivery
    stats + customer reply thread + ตอบเพิ่ม CTA. Per
    design/taemdee-shop.html → inbox.message."""
    inbox = await db.get(Inbox, inbox_id)
    if inbox is None or inbox.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบข้อความนี้")
    customer = await db.get(Customer, inbox.customer_id)
    campaign = await db.get(DeeReachCampaign, inbox.campaign_id) if inbox.campaign_id else None

    await mark_shop_read(db, inbox)
    replies = await list_replies(db, inbox.id)

    # Customer's relationship snapshot — used by the .ix-cust-strip
    # ("ลูกค้าประจำ · N ครั้ง"). Best-effort; falls back to "—" on
    # missing data.
    visit_count = None
    if customer is not None:
        from app.models import Point
        from sqlmodel import func as _func
        visit_count = (await db.exec(
            select(_func.count()).select_from(Point).where(
                Point.customer_id == customer.id,
                Point.shop_id == shop.id,
            )
        )).one()
        visit_count = int(visit_count or 0)

    # "Ball in whose court" gate — the shop can only reply when the
    # last message wasn't theirs. Initial state (zero replies after a
    # broadcast) counts as shop-having-spoken, so the form stays hidden
    # until the customer replies. Prevents the operator from stacking
    # follow-ups on a customer who hasn't responded yet.
    can_reply = bool(replies) and replies[-1].sender == "customer"

    return templates.TemplateResponse(
        request=request,
        name="shop/messages_thread.html",
        context={
            "shop": shop,
            "customer": customer,
            "inbox": inbox,
            "campaign": campaign,
            "replies": replies,
            "visit_count": visit_count,
            "can_reply": can_reply,
        },
    )


@router.post("/messages/{inbox_id}/reply")
async def shop_messages_reply(
    inbox_id: UUID,
    body: str = Form(""),
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_session),
):
    inbox = await db.get(Inbox, inbox_id)
    if inbox is None or inbox.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบข้อความนี้")

    text = (body or "").strip()
    if not text:
        return RedirectResponse(
            url=f"/shop/messages/{inbox_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    await send_reply(db, inbox, sender="shop", body=text)
    return RedirectResponse(
        url=f"/shop/messages/{inbox_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/messages/broadcast/{campaign_id}", response_class=HTMLResponse)
async def shop_broadcast_stats(
    request: Request,
    campaign_id: UUID,
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_session),
):
    """Engagement stats for one broadcast — reads off the DeeReachEvent
    log (audience / opened / replied / voucher_claimed) + the inbox rows
    themselves for the per-customer drill-down. Linked from the
    /shop/messages list's ibh-link "ดู →" so the operator can review
    how a campaign landed without leaving the messages tab."""
    campaign = await db.get(DeeReachCampaign, campaign_id)
    if campaign is None or campaign.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบข้อความนี้")

    # Pull all inbox rows for this campaign — drives the audience
    # total + per-customer drill-down. audience_count on the campaign
    # is the snapshot at send time, which can drift from reality if
    # rows got purged; we prefer the live count.
    inbox_rows = (await db.exec(
        select(Inbox)
        .where(Inbox.campaign_id == campaign_id, Inbox.shop_id == shop.id)
        .order_by(Inbox.created_at.desc())
    )).all()
    audience = len(inbox_rows)

    # Engagement aggregates — distinct customers per event kind so
    # one customer who opens twice doesn't double-count. The events
    # table indexes (campaign_id, kind) so this is cheap.
    events = (await db.exec(
        select(DeeReachEvent).where(DeeReachEvent.campaign_id == campaign_id)
    )).all()
    opened_customers: set = set()
    replied_customers: set = set()
    voucher_customers: set = set()
    last_event_by_customer: dict = {}  # (cust_id, kind) → datetime
    for e in events:
        if e.kind == "opened":
            opened_customers.add(e.customer_id)
        elif e.kind == "replied":
            replied_customers.add(e.customer_id)
        elif e.kind == "voucher_claimed":
            voucher_customers.add(e.customer_id)
        key = (e.customer_id, e.kind)
        prev = last_event_by_customer.get(key)
        if prev is None or e.created_at > prev:
            last_event_by_customer[key] = e.created_at

    # Hydrate customers in one query so the drill-down list doesn't
    # lazy-load N+1.
    customer_by_id: dict = {}
    if inbox_rows:
        cids = [r.customer_id for r in inbox_rows]
        cs = (await db.exec(
            select(Customer).where(Customer.id.in_(cids))
        )).all()
        customer_by_id = {c.id: c for c in cs}

    # Per-customer row view — sort the engaged ones first (replied >
    # opened > nothing) so the operator scans the wins before the
    # silence. Within each band, fall back to most-recent-open then
    # inbox creation order.
    def _engagement_rank(cid):
        if cid in replied_customers:
            return 0
        if cid in opened_customers:
            return 1
        return 2

    rows_view = []
    for r in inbox_rows:
        c = customer_by_id.get(r.customer_id)
        rows_view.append({
            "inbox": r,
            "customer": c,
            "name": (c and c.display_name) or "ลูกค้า",
            "opened": r.customer_id in opened_customers,
            "replied": r.customer_id in replied_customers,
            "voucher_claimed": r.customer_id in voucher_customers,
            "opened_at": last_event_by_customer.get((r.customer_id, "opened")),
            "replied_at": last_event_by_customer.get((r.customer_id, "replied")),
            "rank": _engagement_rank(r.customer_id),
        })
    rows_view.sort(key=lambda v: (v["rank"], v["inbox"].created_at), reverse=False)

    # Reply count (not unique customers — every reply event) — gives
    # the operator a feel for conversation depth on top of reach.
    reply_total = sum(1 for e in events if e.kind == "replied")

    _is_owner = bool(request.state.staff and request.state.staff.is_owner)
    s3_top = await s3_top_context(db, shop, is_owner=_is_owner)
    return templates.TemplateResponse(
        request=request,
        name="shop/broadcast_stats.html",
        context={
            "shop": shop,
            "campaign": campaign,
            "kind_label": _DEEREACH_KIND_LABELS.get(campaign.kind, "ส่งให้ลูกค้า"),
            "audience": audience,
            "opened_count": len(opened_customers),
            "replied_count": len(replied_customers),
            "voucher_count": len(voucher_customers),
            "reply_total": reply_total,
            "rows": rows_view,
            **s3_top,
        },
    )
