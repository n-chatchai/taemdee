"""Staff-side issuance: shop_scan / phone_entry, and the 60-second voids."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from app.core.templates import templates
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import (
    SessionContext,
    get_current_shop,
    get_current_staff,
    require_permission,
)
from app.core.database import get_session
from app.models import Customer, Redemption, Shop, Point, StaffMember
from app.models.util import utcnow
from app.services.events import feed_row_html, publish, point_toast_html
from app.services.issuance import IssuanceError, issue_point, void_point
from app.services.redemption import active_point_count, void_redemption

router = APIRouter()

VOID_WINDOW_SECONDS = 60


@router.get("/issue", response_class=HTMLResponse)
async def issue_page(request: Request, shop: Shop = Depends(get_current_shop)):
    return templates.TemplateResponse(
        request=request,
        name="shop/issue.html",
        context={"shop": shop},
    )


@router.get("/issue/phone", response_class=HTMLResponse)
async def issue_phone_page(request: Request, shop: Shop = Depends(get_current_shop)):
    """S3.phone — manual issuance via custom numpad. POSTs back to /shop/issue."""
    return templates.TemplateResponse(
        request=request,
        name="shop/issue_phone.html",
        context={"shop": shop},
    )


@router.get("/issue/search", response_class=HTMLResponse)
async def issue_search_page(request: Request, shop: Shop = Depends(get_current_shop)):
    """S3.search — search a known customer by name/phone and grant N stamps."""
    return templates.TemplateResponse(
        request=request,
        name="shop/issue_search.html",
        context={"shop": shop},
    )


@router.get("/issue/search/customers")
async def search_customers(
    q: str,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """Returns up to 8 claimed customers matching `q` against display_name or
    phone. Anonymous customers aren't surfaced (they have no contact info to
    distinguish). Each row carries the active stamp count at this shop and
    the most-recent visit timestamp so the search list can show context.
    """
    q = (q or "").strip()
    if not q:
        return {"results": []}
    like = f"%{q}%"
    result = await db.exec(
        select(Customer)
        .where(
            Customer.is_anonymous == False,  # noqa: E712
            ((Customer.display_name.ilike(like)) | (Customer.phone.ilike(like))),
        )
        .limit(8)
    )
    customers = list(result.all())
    out = []
    for c in customers:
        active = (await db.exec(
            select(func.count())
            .select_from(Point)
            .where(
                Point.shop_id == shop.id,
                Point.customer_id == c.id,
                Point.is_voided == False,  # noqa: E712
                Point.redemption_id.is_(None),
            )
        )).one()
        last_visit = (await db.exec(
            select(Point.created_at)
            .where(Point.shop_id == shop.id, Point.customer_id == c.id)
            .order_by(Point.created_at.desc())
            .limit(1)
        )).first()
        out.append({
            "id": str(c.id),
            "display_name": c.display_name or "ลูกค้า",
            "phone": c.phone or "",
            "active_points": active,
            "last_visit_iso": last_visit.isoformat() if last_visit else None,
        })
    return {"results": out}


@router.post("/issue/search/grant")
async def issue_search_grant(
    customer_id: UUID = Form(...),
    points: int = Form(1),
    shop: Shop = Depends(get_current_shop),
    staff: Optional[StaffMember] = Depends(get_current_staff),
    db: AsyncSession = Depends(get_session),
):
    """Issues `points` stamps (capped 1–10) to the picked customer. Each stamp
    fires the SSE feed-row + toast pipeline, just like a customer scan.
    Uses method='system' to bypass cooldown — manual grants are intentional.
    """
    customer = await db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    points = max(1, min(int(points or 1), 10))

    issued_ids = []
    for _ in range(points):
        try:
            stamp = await issue_point(
                db, shop, customer,
                method="system",
                staff_id=staff.id if staff else None,
            )
        except IssuanceError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
        issued_ids.append(str(stamp.id))
        publish(
            shop.id,
            "feed-row",
            feed_row_html("point", stamp.id, stamp.created_at.strftime("%H:%M")),
        )

    new_count = await active_point_count(db, shop.id, customer.id)
    publish(
        shop.id,
        "point-toast",
        point_toast_html(stamp.id, new_count, shop.reward_threshold),
    )
    return {"granted": points, "stamp_ids": issued_ids, "customer_id": str(customer.id)}


@router.post("/issue/methods")
async def save_issuance_methods(
    shop_scan: str = Form("0"),
    phone_entry: str = Form("0"),
    search: str = Form("0"),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """S5 settings — persist the manual-issuance toggles. customer_scan is
    implicit (every shop has a printable QR); only the 3 opt-in methods
    are stored.
    """
    shop.issue_method_shop_scan = shop_scan == "1"
    shop.issue_method_phone_entry = phone_entry == "1"
    shop.issue_method_search = search == "1"
    db.add(shop)
    await db.commit()
    return RedirectResponse(url="/shop/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/issue")
async def staff_issue_point(
    method: str = Form(...),
    customer_id: Optional[UUID] = Form(None),
    phone: Optional[str] = Form(None),
    branch_id: Optional[UUID] = Form(None),
    shop: Shop = Depends(get_current_shop),
    staff: Optional[StaffMember] = Depends(get_current_staff),
    db: AsyncSession = Depends(get_session),
):
    """Issue a stamp via shop_scan (provide customer_id) or phone_entry (provide phone).

    For phone_entry, a Customer is created if none exists for that phone (claimed account).
    """
    if method == "shop_scan":
        if not customer_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "customer_id is required for shop_scan")
        customer = await db.get(Customer, customer_id)
        if not customer:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    elif method == "phone_entry":
        if not phone:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "phone is required for phone_entry")
        result = await db.exec(select(Customer).where(Customer.phone == phone))
        customer = result.first()
        if not customer:
            customer = Customer(is_anonymous=False, phone=phone)
            db.add(customer)
            await db.commit()
            await db.refresh(customer)
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "method must be 'shop_scan' or 'phone_entry'"
        )

    try:
        stamp = await issue_point(
            db, shop, customer,
            method=method,
            branch_id=branch_id,
            staff_id=staff.id if staff else None,
        )
    except IssuanceError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    publish(
        shop.id,
        "feed-row",
        feed_row_html("point", stamp.id, stamp.created_at.strftime("%H:%M")),
    )
    new_count = await active_point_count(db, shop.id, customer.id)
    publish(
        shop.id,
        "point-toast",
        point_toast_html(stamp.id, new_count, shop.reward_threshold),
    )
    return {"stamp_id": str(stamp.id), "customer_id": str(customer.id)}


@router.post("/issue/manual")
async def staff_issue_manual_stamp(
    branch_id: Optional[UUID] = Form(None),
    shop: Shop = Depends(get_current_shop),
    staff: Optional[StaffMember] = Depends(get_current_staff),
    db: AsyncSession = Depends(get_session),
):
    """One-tap stamp for walk-in customers (no phone, no QR, no card).

    Creates a fresh anonymous Customer per call so each manual stamp counts as
    a distinct walk-in in the dashboard's "ลูกค้ากลับมา" headline. The customer
    is throwaway — no contact path back — so this is best-effort attribution.
    Same SSE pipe as the other issuance methods, so the live toast still fires.
    """
    customer = Customer(is_anonymous=True)
    db.add(customer)
    await db.commit()
    await db.refresh(customer)

    try:
        stamp = await issue_point(
            db, shop, customer,
            method="shop_scan",
            branch_id=branch_id,
            staff_id=staff.id if staff else None,
        )
    except IssuanceError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    publish(
        shop.id,
        "feed-row",
        feed_row_html("point", stamp.id, stamp.created_at.strftime("%H:%M")),
    )
    new_count = await active_point_count(db, shop.id, customer.id)
    publish(
        shop.id,
        "point-toast",
        point_toast_html(stamp.id, new_count, shop.reward_threshold),
    )
    return {"stamp_id": str(stamp.id), "customer_id": str(customer.id)}


@router.post("/stamps/{stamp_id}/void")
async def void_point_route(
    stamp_id: UUID,
    ctx: SessionContext = Depends(require_permission("can_void")),
    db: AsyncSession = Depends(get_session),
):
    stamp = await db.get(Point, stamp_id)
    if not stamp:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Point not found")
    if stamp.shop_id != ctx.shop_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-shop access denied")

    age_seconds = (utcnow() - stamp.created_at).total_seconds()
    if age_seconds > VOID_WINDOW_SECONDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Void window expired ({VOID_WINDOW_SECONDS}s)",
        )

    await void_point(db, stamp, by_staff_id=ctx.staff_id)
    publish(ctx.shop_id, "void", f'<span data-row="row-{stamp.id}"></span>')
    return {"voided": True, "stamp_id": str(stamp.id)}


@router.post("/redemptions/{redemption_id}/void")
async def void_redemption_route(
    redemption_id: UUID,
    ctx: SessionContext = Depends(require_permission("can_void")),
    db: AsyncSession = Depends(get_session),
):
    redemption = await db.get(Redemption, redemption_id)
    if not redemption:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Redemption not found")
    if redemption.shop_id != ctx.shop_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-shop access denied")

    age_seconds = (utcnow() - redemption.created_at).total_seconds()
    if age_seconds > VOID_WINDOW_SECONDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Void window expired ({VOID_WINDOW_SECONDS}s)",
        )

    await void_redemption(db, redemption, by_staff_id=ctx.staff_id)
    publish(ctx.shop_id, "void", f'<span data-row="row-{redemption.id}"></span>')
    return {"voided": True, "redemption_id": str(redemption.id)}
