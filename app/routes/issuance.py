"""Staff-side issuance: shop_scan / phone_entry, and stamp voids."""

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
from app.models.util import bkk_feed_time, bkk_hms, utcnow
from app.services.branch import s3_top_context
from app.services.events import feed_row_html, publish
from app.services.issuance import IssuanceError, issue_point, void_point
from app.services.redemption import active_point_count, void_redemption

router = APIRouter()


@router.get("/issue", response_class=HTMLResponse)
async def issue_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """S3.issue — full-page ออกแต้ม hub: recent feed + 3 method buttons.
    Replaces the old /shop/issue page (which was the methods toggle config).
    The toggle settings now live at /shop/issue/methods."""
    from app.models import Customer, Redemption
    from app.core.config import settings as app_settings

    feed_cap = app_settings.shop_customer_last_scan_display_number

    recent_points = (await db.exec(
        select(Point).where(Point.shop_id == shop.id)
        .order_by(Point.created_at.desc()).limit(feed_cap)
    )).all()
    recent_redemptions = (await db.exec(
        select(Redemption).where(Redemption.shop_id == shop.id)
        .order_by(Redemption.created_at.desc()).limit(feed_cap)
    )).all()

    customer_ids = {p.customer_id for p in recent_points} | {r.customer_id for r in recent_redemptions}
    customers_by_id = {}
    if customer_ids:
        rows = (await db.exec(select(Customer).where(Customer.id.in_(customer_ids)))).all()
        customers_by_id = {c.id: (c.display_name or "ลูกค้า") for c in rows}
    feed = sorted(
        [("point", p, customers_by_id.get(p.customer_id, "ลูกค้า")) for p in recent_points]
        + [("redemption", r, customers_by_id.get(r.customer_id, "ลูกค้า")) for r in recent_redemptions],
        key=lambda x: x[1].created_at,
        reverse=True,
    )[:feed_cap]

    s3_top = await s3_top_context(db, shop)
    return templates.TemplateResponse(
        request=request,
        name="shop/issue.html",
        context={
            "shop": shop,
            "feed": feed,
            "feed_cap": feed_cap,
            **s3_top,
        },
    )


@router.get("/issue/methods", response_class=HTMLResponse)
async def issue_methods_page(request: Request, shop: Shop = Depends(get_current_shop)):
    """S5 — toggle which issuance methods this shop accepts. The form POSTs
    back to the existing /shop/issue/methods handler below."""
    return templates.TemplateResponse(
        request=request,
        name="shop/issue_methods.html",
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


@router.get("/issue/scan", response_class=HTMLResponse)
async def issue_scan_page(request: Request, shop: Shop = Depends(get_current_shop)):
    """S3.scan — camera viewfinder page. Decodes the customer's identity QR
    (from /my-id) and POSTs to /shop/issue/scan to issue a stamp.
    """
    return templates.TemplateResponse(
        request=request,
        name="shop/issue_scan.html",
        context={"shop": shop},
    )


_CUSTOMER_ID_PATH_PREFIX = "/c/"


@router.post("/issue/scan")
async def issue_scan_grant(
    scanned_value: str = Form(...),
    shop: Shop = Depends(get_current_shop),
    staff: Optional[StaffMember] = Depends(get_current_staff),
    db: AsyncSession = Depends(get_session),
):
    """Convert a scanned QR string into a point via the shop_scan method.

    Accepts the URL the customer's /my-id page encodes — `https://<host>/c/<uuid>`.
    Anything else is rejected with a clear error so the staff knows the QR
    wasn't a TaemDee customer card.
    """
    raw = (scanned_value or "").strip()
    if not raw:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "ไม่พบข้อมูลใน QR ที่สแกน — ลองใหม่อีกครั้ง",
        )

    # Pull /c/<uuid> out of whatever URL the QR contained.
    idx = raw.find(_CUSTOMER_ID_PATH_PREFIX)
    if idx < 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "QR นี้ไม่ใช่บัตรลูกค้าแต้มดี — ลองใช้วิธี 'กรอกเบอร์' แทน",
        )
    suffix = raw[idx + len(_CUSTOMER_ID_PATH_PREFIX):]
    customer_id_str = suffix.split("/")[0].split("?")[0].strip()
    try:
        customer_uuid = UUID(customer_id_str)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"รหัสในบัตร QR ไม่ถูกต้อง ({customer_id_str[:24]}…) — กรุณาให้ลูกค้ารีเฟรชหน้า QR ของตัวเองก่อน",
        )

    customer = await db.get(Customer, customer_uuid)
    if not customer:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "ไม่พบลูกค้าตามบัตรนี้ — บัญชีอาจถูกลบไปแล้ว",
        )

    # Voucher serve-on-scan: if this customer has an unserved redemption at
    # this shop in the last 30 minutes, the staff is almost certainly here
    # to serve the reward — flip served_at instead of issuing a fresh
    # stamp. Avoids the awkward "stamp the customer who's collecting their
    # free coffee" race + powers C5 "✓ ใช้แล้ว HH:MM" state.
    from datetime import timedelta
    served_window_start = utcnow() - timedelta(minutes=30)
    pending_redemption = (await db.exec(
        select(Redemption).where(
            Redemption.customer_id == customer.id,
            Redemption.shop_id == shop.id,
            Redemption.is_voided == False,  # noqa: E712
            Redemption.served_at.is_(None),
            Redemption.created_at >= served_window_start,
        )
        .order_by(Redemption.created_at.desc())
    )).first()
    if pending_redemption is not None:
        pending_redemption.served_at = utcnow()
        pending_redemption.served_by_staff_id = staff.id if staff else None
        db.add(pending_redemption)
        await db.commit()
        publish(
            shop.id,
            "feed-row",
            feed_row_html(
                "redemption", pending_redemption.id,
                bkk_feed_time(pending_redemption.served_at),
                customer.display_name or "ลูกค้า",
            ),
        )
        return {
            "served_redemption_id": str(pending_redemption.id),
            "customer_id": str(customer.id),
            "customer_name": customer.display_name or "ลูกค้า",
            "reward_description": shop.reward_description,
        }

    try:
        point = await issue_point(
            db, shop, customer,
            method="shop_scan",
            staff_id=staff.id if staff else None,
        )
    except IssuanceError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    publish(
        shop.id,
        "feed-row",
        feed_row_html("point", point.id, bkk_feed_time(point.created_at), customer.display_name or "ลูกค้า"),
    )
    return {
        "point_id": str(point.id),
        "customer_id": str(customer.id),
        "customer_name": customer.display_name or "ลูกค้า",
    }


@router.get("/issue/grant", response_class=HTMLResponse)
async def issue_grant_page(request: Request, shop: Shop = Depends(get_current_shop)):
    """S3.grant — search a known customer by name/phone and grant N points."""
    return templates.TemplateResponse(
        request=request,
        name="shop/issue_grant.html",
        context={"shop": shop},
    )


@router.get("/issue/grant/customers")
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


@router.post("/issue/grant")
async def issue_grant_action(
    customer_id: UUID = Form(...),
    points: int = Form(1),
    shop: Shop = Depends(get_current_shop),
    staff: Optional[StaffMember] = Depends(get_current_staff),
    db: AsyncSession = Depends(get_session),
):
    """Issues `points` points (capped 1–10) to the picked customer. Each point
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
            point = await issue_point(
                db, shop, customer,
                method="system",
                staff_id=staff.id if staff else None,
            )
        except IssuanceError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
        issued_ids.append(str(point.id))
        publish(
            shop.id,
            "feed-row",
            feed_row_html("point", point.id, bkk_feed_time(point.created_at), customer.display_name or "ลูกค้า"),
        )
    return {"granted": points, "point_ids": issued_ids, "customer_id": str(customer.id)}


@router.post("/issue/methods")
async def save_issuance_methods(
    shop_scan: str = Form("0"),
    phone_entry: str = Form("0"),
    grant: str = Form("0"),
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """S5 settings — persist the manual-issuance toggles. customer_scan is
    implicit (every shop has a printable QR); only the 3 opt-in methods
    are stored.
    """
    shop.issue_method_shop_scan = shop_scan == "1"
    shop.issue_method_phone_entry = phone_entry == "1"
    shop.issue_method_grant = grant == "1"
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
    """Issue a point via shop_scan (provide customer_id) or phone_entry (provide phone).

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
        point = await issue_point(
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
        feed_row_html("point", point.id, bkk_feed_time(point.created_at), customer.display_name or "ลูกค้า"),
    )
    return {"point_id": str(point.id), "customer_id": str(customer.id)}


@router.post("/issue/manual")
async def staff_issue_manual_point(
    branch_id: Optional[UUID] = Form(None),
    shop: Shop = Depends(get_current_shop),
    staff: Optional[StaffMember] = Depends(get_current_staff),
    db: AsyncSession = Depends(get_session),
):
    """One-tap point for walk-in customers (no phone, no QR, no card).

    Creates a fresh anonymous Customer per call so each manual point counts as
    a distinct walk-in in the dashboard's "ลูกค้ากลับมา" headline. The customer
    is throwaway — no contact path back — so this is best-effort attribution.
    Same SSE pipe as the other issuance methods, so the live toast still fires.
    """
    customer = Customer(is_anonymous=True)
    db.add(customer)
    await db.commit()
    await db.refresh(customer)

    try:
        point = await issue_point(
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
        feed_row_html("point", point.id, bkk_feed_time(point.created_at), customer.display_name or "ลูกค้า"),
    )
    return {"point_id": str(point.id), "customer_id": str(customer.id)}


@router.get("/feed/{kind}/{item_id}")
async def feed_detail(
    kind: str,
    item_id: UUID,
    shop: Shop = Depends(get_current_shop),
    db: AsyncSession = Depends(get_session),
):
    """JSON payload for the S3.detail bottom sheet — shop taps a feed
    row in the dock, the dashboard fetches this, and renders the sheet
    with customer info, activity meta and a countdown to the void cutoff."""
    if kind == "point":
        item = await db.get(Point, item_id)
    elif kind == "redemption":
        item = await db.get(Redemption, item_id)
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "kind ต้องเป็น point หรือ redemption")
    if not item or item.shop_id != shop.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ไม่พบกิจกรรมที่ระบุ")

    customer = await db.get(Customer, item.customer_id)
    voidable = not item.is_voided

    if kind == "point":
        method_th = {
            "customer_scan": "ลูกค้าสแกน QR",
            "shop_scan": "ร้านสแกน QR ลูกค้า",
            "phone_entry": "กรอกเบอร์ลูกค้า",
            "system": "ระบบ (ให้แต้ม / โปร)",
        }.get(item.issuance_method, item.issuance_method or "—")
    else:
        method_th = "รับรางวัล"

    issuer = "—"
    if getattr(item, "issued_by_staff_id", None):
        staff = await db.get(StaffMember, item.issued_by_staff_id)
        if staff:
            issuer = staff.display_name or "พนักงาน"
    elif kind == "point" and item.issuance_method == "customer_scan":
        issuer = "ลูกค้า (สแกนเอง)"

    weekday_th = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")[item.created_at.weekday()]
    month_th = ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.")[item.created_at.month - 1]
    time_full = f"{bkk_hms(item.created_at)} · {weekday_th} {item.created_at.day} {month_th}"

    new_count = await active_point_count(db, shop.id, item.customer_id)

    return {
        "kind": kind,
        "id": str(item.id),
        "customer_name": (customer.display_name if customer else None) or "ลูกค้า",
        "customer_phone": (customer.phone if customer else None) or "—",
        "is_anonymous": bool(customer.is_anonymous) if customer else True,
        "activity": "1 แต้ม" if kind == "point" else "รับรางวัล",
        "is_point": kind == "point",
        "time_full": time_full,
        "method_th": method_th,
        "issuer": issuer,
        "current_count": new_count,
        "threshold": shop.reward_threshold,
        "is_voided": item.is_voided,
        "voidable": voidable,
        "void_url": f"/shop/{'points' if kind == 'point' else 'redemptions'}/{item_id}/void",
    }


@router.post("/points/{point_id}/void")
async def void_point_route(
    point_id: UUID,
    ctx: SessionContext = Depends(require_permission("can_void")),
    db: AsyncSession = Depends(get_session),
):
    point = await db.get(Point, point_id)
    if not point:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Point not found")
    if point.shop_id != ctx.shop_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-shop access denied")

    await void_point(db, point, by_staff_id=ctx.staff_id)
    publish(ctx.shop_id, "void", f'<span data-row="row-{point.id}"></span>')
    return {"voided": True, "point_id": str(point.id)}


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

    await void_redemption(db, redemption, by_staff_id=ctx.staff_id)
    publish(ctx.shop_id, "void", f'<span data-row="row-{redemption.id}"></span>')
    return {"voided": True, "redemption_id": str(redemption.id)}
