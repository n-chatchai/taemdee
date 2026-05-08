"""Customer ↔ Shop chat — customer-side endpoints.

Search shops, render a single conversation, send a message. Shop-side
endpoints live in routes/shop_chat.py (separate file because the
auth model + perms differ — customer side is connect-only, shop side
gates on a session + can_settings-style perm)."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import CUSTOMER_COOKIE_NAME, find_or_create_customer
from app.core.database import get_session
from app.core.templates import templates
from app.models import Shop
from app.services.customer_chat import (
    RateLimited,
    get_or_create_thread,
    list_messages,
    list_threads_for_customer,
    mark_read,
    search_shops,
    send_message,
)

router = APIRouter()


@router.get("/find-shops", response_class=HTMLResponse)
async def find_shops_page(
    request: Request,
    q: Optional[str] = None,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Customer-facing shop search. Renders an empty state when q is
    blank so first-time arrival doesn't immediately query for every
    shop in the system."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    results = []
    if q and q.strip():
        results = await search_shops(db, q.strip())
    return templates.TemplateResponse(
        request=request,
        name="customer/find_shops.html",
        context={
            "customer": customer,
            "q": q or "",
            "results": results,
        },
    )


@router.get("/messages", response_class=HTMLResponse)
async def my_conversations(
    request: Request,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Customer's thread list — every shop they've ever messaged,
    most-recent first, with the unread badge. Rows link into the
    single-thread view."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    threads = await list_threads_for_customer(db, customer.id)

    shop_by_id = {}
    if threads:
        from sqlmodel import select
        shop_ids = [t.shop_id for t in threads]
        rows = (await db.exec(
            select(Shop).where(Shop.id.in_(shop_ids))
        )).all()
        shop_by_id = {s.id: s for s in rows}

    return templates.TemplateResponse(
        request=request,
        name="customer/messages_list.html",
        context={
            "customer": customer,
            "threads": threads,
            "shop_by_id": shop_by_id,
        },
    )


@router.get("/messages/{shop_id}", response_class=HTMLResponse)
async def my_conversation(
    request: Request,
    shop_id: UUID,
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    """Single thread (customer view): existing messages oldest-first
    + a compose box. Opening the thread zeroes the customer-side
    unread count."""
    customer, _ = await find_or_create_customer(customer_cookie, db)
    shop = await db.get(Shop, shop_id)
    if shop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบร้านนี้")

    thread = await get_or_create_thread(
        db, customer_id=customer.id, shop_id=shop.id,
    )
    await mark_read(db, thread, by="customer")
    messages = await list_messages(db, thread.id)

    return templates.TemplateResponse(
        request=request,
        name="customer/messages_thread.html",
        context={
            "customer": customer,
            "shop": shop,
            "thread": thread,
            "messages": messages,
        },
    )


@router.post("/messages/{shop_id}")
async def send_to_shop(
    shop_id: UUID,
    body: str = Form(""),
    customer_cookie: Optional[str] = Cookie(None, alias=CUSTOMER_COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
):
    customer, _ = await find_or_create_customer(customer_cookie, db)
    shop = await db.get(Shop, shop_id)
    if shop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบร้านนี้")

    text = (body or "").strip()
    if not text:
        return RedirectResponse(
            url=f"/messages/{shop_id}", status_code=status.HTTP_303_SEE_OTHER,
        )

    thread = await get_or_create_thread(
        db, customer_id=customer.id, shop_id=shop.id,
    )
    try:
        await send_message(db, thread, sender="customer", body=text)
    except RateLimited as e:
        # Phase 1 surfaces the 429 inline by re-rendering the thread
        # with an error banner. For now use the simple flash via
        # query string so the GET handler can read it.
        return RedirectResponse(
            url=f"/messages/{shop_id}?rate_limited=1",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/messages/{shop_id}", status_code=status.HTTP_303_SEE_OTHER,
    )
