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
from app.models import Customer, CustomerThread, Shop
from app.services.customer_chat import (
    list_messages,
    list_threads_for_shop,
    mark_read,
    send_message,
)

router = APIRouter()


@router.get("/messages", response_class=HTMLResponse)
async def shop_messages_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    _: SessionContext = Depends(get_session_context),
    db: AsyncSession = Depends(get_session),
):
    threads = await list_threads_for_shop(db, shop.id)
    customer_by_id = {}
    if threads:
        from sqlmodel import select
        cids = [t.customer_id for t in threads]
        rows = (await db.exec(
            select(Customer).where(Customer.id.in_(cids))
        )).all()
        customer_by_id = {c.id: c for c in rows}
    return templates.TemplateResponse(
        request=request,
        name="shop/messages_list.html",
        context={
            "shop": shop,
            "threads": threads,
            "customer_by_id": customer_by_id,
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
