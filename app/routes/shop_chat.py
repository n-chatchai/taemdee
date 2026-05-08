"""Shop-side endpoints for customer ↔ shop chat.

Replaces the dock's "ออกแต้ม" tab as the new "ข้อความ" entry point.
List of conversations, single thread + reply box, and the badge
counter that the dock partial reads to render unread state."""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import (
    SessionContext,
    get_current_shop,
    get_session_context,
)
from app.core.database import get_session
from app.core.templates import templates
from app.models import Customer, CustomerMessage, CustomerThread, DeeReachCampaign, Shop
from app.services.branch import s3_top_context
from app.services.customer_chat import (
    list_messages,
    list_threads_for_shop,
    mark_read,
    send_message,
)

router = APIRouter()


_DEEREACH_KIND_LABELS = {
    "win_back": "ชวนกลับ",
    "almost_there": "ใกล้ครบ",
    "unredeemed_reward": "เตือนรับรางวัล",
    "new_customer": "ขอบคุณลูกค้าใหม่",
    "birthday": "อวยพรวันเกิด",
    "manual": "ข้อความเอง",
}


@router.get("/messages", response_class=HTMLResponse)
async def shop_messages_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_session),
):
    """Unified shop ข้อความ — customer chat threads (two-way) AND
    DeeReach broadcast campaigns the shop has sent. Sorted by most-
    recent activity. Tapping a thread opens /shop/messages/{thread_id}
    (compose + reply); tapping a campaign opens
    /shop/deereach/sent?campaign_id={id} (the existing confirmation /
    audit page)."""
    from sqlmodel import select

    threads = await list_threads_for_shop(db, shop.id)
    customer_by_id = {}
    if threads:
        cids = [t.customer_id for t in threads]
        rows = (await db.exec(
            select(Customer).where(Customer.id.in_(cids))
        )).all()
        customer_by_id = {c.id: c for c in rows}

    # DeeReach campaigns this shop has actually sent — `sent_at IS NOT
    # NULL` filters out drafts / failed locks. Cap at 50 to keep the
    # list responsive on a heavy sender.
    campaigns = (await db.exec(
        select(DeeReachCampaign)
        .where(
            DeeReachCampaign.shop_id == shop.id,
            DeeReachCampaign.sent_at.is_not(None),
        )
        .order_by(DeeReachCampaign.sent_at.desc())
        .limit(50)
    )).all()

    # Last-message preview per thread — single batched query keyed by
    # thread_id so we render a "พี่: ..." / "เรา: ..." line under
    # each row without N+1.
    last_msg_by_thread = {}
    if threads:
        thread_ids = [t.id for t in threads]
        msg_rows = (await db.exec(
            select(CustomerMessage)
            .where(CustomerMessage.thread_id.in_(thread_ids))
            .order_by(CustomerMessage.thread_id, CustomerMessage.created_at.desc())
        )).all()
        for m in msg_rows:
            last_msg_by_thread.setdefault(m.thread_id, m)

    items = []
    for t in threads:
        c = customer_by_id.get(t.customer_id)
        last = last_msg_by_thread.get(t.id)
        if last is None:
            preview = "ยังไม่มีข้อความ"
        else:
            prefix = "พี่: " if last.sender == "customer" else "เรา: "
            preview = prefix + (last.body or "[แนบไฟล์]")
        items.append({
            "kind": "thread",
            "thread": t,
            "customer": c,
            "name": (c and c.display_name) or "ลูกค้า",
            "preview": preview,
            "sort_at": t.last_at,
            "href": f"/shop/messages/{t.id}",
            "unread": int(t.shop_unread or 0),
        })
    for cp in campaigns:
        items.append({
            "kind": "campaign",
            "campaign": cp,
            "name": _DEEREACH_KIND_LABELS.get(cp.kind, "ส่งให้ลูกค้า"),
            "preview": cp.message_text or "",
            "audience": cp.audience_count,
            "sort_at": cp.sent_at,
            "href": f"/shop/deereach/sent?campaign_id={cp.id}",
            "unread": 0,
        })
    items.sort(key=lambda it: it["sort_at"], reverse=True)

    s3_top = await s3_top_context(db, shop)
    return templates.TemplateResponse(
        request=request,
        name="shop/messages_list.html",
        context={
            "shop": shop,
            "threads": threads,
            "customer_by_id": customer_by_id,
            "items": items,
            **s3_top,
        },
    )


@router.get("/messages/{thread_id}", response_class=HTMLResponse)
async def shop_messages_thread(
    request: Request,
    thread_id: UUID,
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_session),
):
    thread = await db.get(CustomerThread, thread_id)
    if thread is None or thread.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบบทสนทนานี้")
    customer = await db.get(Customer, thread.customer_id)

    await mark_read(db, thread, by="shop")
    messages = await list_messages(db, thread.id)

    return templates.TemplateResponse(
        request=request,
        name="shop/messages_thread.html",
        context={
            "shop": shop,
            "customer": customer,
            "thread": thread,
            "messages": messages,
        },
    )


@router.post("/messages/{thread_id}/reply")
async def shop_messages_reply(
    thread_id: UUID,
    body: str = Form(""),
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_session),
):
    thread = await db.get(CustomerThread, thread_id)
    if thread is None or thread.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบบทสนทนานี้")

    text = (body or "").strip()
    if not text:
        return RedirectResponse(
            url=f"/shop/messages/{thread_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    await send_message(db, thread, sender="shop", body=text)
    return RedirectResponse(
        url=f"/shop/messages/{thread_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
