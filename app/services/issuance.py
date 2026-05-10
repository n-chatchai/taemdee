"""Point issuance — the core act across all 3 methods, plus void.

Anti-rescan policy is per-shop: `Shop.scan_cooldown_minutes` (default 0 = no
cooldown). When > 0, a customer must wait that many minutes between stamps at
the same shop. Geofence + pattern alerts are deferred.
"""

import logging
from datetime import timedelta
from typing import Optional, Tuple
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Customer, Redemption, Shop, Point
from app.models.util import utcnow

log = logging.getLogger(__name__)

VALID_METHODS = {"customer_scan", "shop_scan", "phone_entry", "system"}


class IssuanceError(Exception):
    pass


async def _has_recent_stamp(
    db: AsyncSession, shop_id: UUID, customer_id: UUID, since,
) -> bool:
    """True if a non-voided stamp exists for (shop, customer) at or after `since`."""
    result = await db.exec(
        select(Point.id)
        .where(
            Point.shop_id == shop_id,
            Point.customer_id == customer_id,
            Point.created_at >= since,
            Point.is_voided == False,  # noqa: E712
        )
        .limit(1)
    )
    return result.first() is not None


async def issue_point(
    db: AsyncSession,
    shop: Shop,
    customer: Customer,
    method: str,
    *,
    branch_id: Optional[UUID] = None,
    staff_id: Optional[UUID] = None,
    grant_id: Optional[UUID] = None,
) -> Tuple[Point, Optional[Redemption]]:
    """Issue a stamp; auto-redeem if it brings the customer to threshold.

    `method`: customer_scan | shop_scan | phone_entry | system.
    `system` bypasses the cooldown (bonus/birthday/admin stamps).

    Returns `(stamp, redemption)`. `redemption` is None unless this stamp
    pushed the customer's active count to ≥ `shop.reward_threshold` and the
    inline `redeem()` call succeeded; if the redemption attempt hit a
    RedemptionError (e.g. a transient race), the stamp is still committed
    and the caller gets None — the next issuance attempt will retry.

    Auto-redeem lives here (not in routes) so every issuance entry point —
    customer scan, shop-side scan, phone entry, manual insert, DeeReach
    bonus_stamp_count grants — is covered by a single source of truth. A
    new caller can't accidentally skip the threshold check.

    Raises IssuanceError on cooldown violation or invalid branch context.
    """
    if method not in VALID_METHODS:
        raise ValueError(f"Invalid issuance method: {method}")

    # In 'separate' reward mode, a branch must be chosen (we don't know which card to stamp otherwise).
    if shop.reward_mode == "separate" and branch_id is None:
        raise IssuanceError("branch_id is required when shop is in 'separate' reward mode")

    cooldown = shop.scan_cooldown_minutes or 0
    if method != "system" and cooldown > 0:
        threshold = utcnow() - timedelta(minutes=cooldown)
        if await _has_recent_stamp(db, shop.id, customer.id, threshold):
            raise IssuanceError(
                f"Scan cooldown not yet elapsed ({cooldown} min)"
            )

    stamp = Point(
        shop_id=shop.id,
        customer_id=customer.id,
        branch_id=branch_id,
        issuance_method=method,
        issued_by_staff_id=staff_id,
        grant_id=grant_id,
    )
    db.add(stamp)
    await db.commit()
    await db.refresh(stamp)

    redemption = await _maybe_auto_redeem(db, shop, customer, branch_id)
    return stamp, redemption


async def _maybe_auto_redeem(
    db: AsyncSession,
    shop: Shop,
    customer: Customer,
    branch_id: Optional[UUID],
) -> Optional[Redemption]:
    """Fire `redeem()` if the customer just crossed the threshold. Returns
    None when there's nothing to redeem yet, or when the inline redeem
    failed (logged for visibility — the next issuance retries)."""
    # Local import to avoid a circular at module load time
    # (services/redemption may import issuance helpers in the future).
    from app.services.redemption import RedemptionError, active_point_count, redeem

    scope_branch = branch_id if shop.reward_mode == "separate" else None
    count = await active_point_count(db, shop.id, customer.id, scope_branch)
    if count < shop.reward_threshold:
        return None
    try:
        return await redeem(
            db, shop, customer,
            branch_id=branch_id if shop.reward_mode == "separate" else None,
        )
    except RedemptionError as e:
        log.warning(
            "auto-redeem failed for shop=%s customer=%s: %s",
            shop.id, customer.id, e,
        )
        return None


async def void_point(
    db: AsyncSession,
    stamp: Point,
    *,
    by_staff_id: Optional[UUID] = None,
) -> Point:
    """Void a stamp."""
    stamp.is_voided = True
    stamp.voided_at = utcnow()
    stamp.voided_by_staff_id = by_staff_id
    db.add(stamp)
    await db.commit()
    await db.refresh(stamp)
    return stamp


# ---------------------------------------------------------------------------
# Recent activity feed used by /shop/issue (full-page) AND embedded on
# /shop/dashboard. Single helper so both surfaces always produce the
# same feed shape — no drift between the two callsites.
# ---------------------------------------------------------------------------

async def build_recent_feed(db, shop, *, feed_cap: int):
    """Returns the grouped (point + redemption) feed list of tuples
    (kind, item, customer_name, amount, method_th, staff_name) the
    issue.html / dashboard.html templates render. Kept minimal — no
    template knowledge here, just the data."""
    from sqlmodel import select
    from app.models import Customer, Point, Redemption, StaffMember, User

    recent_points = (await db.exec(
        select(Point)
        .where(Point.shop_id == shop.id)
        .order_by(Point.created_at.desc())
        .limit(feed_cap)
    )).all()
    recent_redemptions = (await db.exec(
        select(Redemption)
        .where(Redemption.shop_id == shop.id)
        .order_by(Redemption.created_at.desc())
        .limit(feed_cap)
    )).all()

    customer_ids = {p.customer_id for p in recent_points} | {
        r.customer_id for r in recent_redemptions
    }
    staff_ids = {p.issued_by_staff_id for p in recent_points if p.issued_by_staff_id} | {
        r.served_by_staff_id for r in recent_redemptions if r.served_by_staff_id
    }

    customers_by_id = {}
    if customer_ids:
        rows = (await db.exec(
            select(Customer).where(Customer.id.in_(customer_ids))
        )).all()
        customers_by_id = {c.id: (c.display_name or "ลูกค้า") for c in rows}

    staff_by_id = {}
    if staff_ids:
        # Pull staff + their bound user in one query so we can read
        # the display name without lazy-loading .user (which 500s on
        # an async session). Falls back to display_name_hint (the
        # owner's invite label, set before the seat is claimed) and
        # then to "พนักงาน".
        rows = (await db.exec(
            select(StaffMember, User)
            .join(User, User.id == StaffMember.user_id, isouter=True)
            .where(StaffMember.id.in_(staff_ids))
        )).all()
        staff_by_id = {
            s.id: (
                (u.display_name if u and u.display_name else None)
                or s.display_name_hint
                or "พนักงาน"
            )
            for s, u in rows
        }

    method_th_map = {
        "customer_scan": "ลูกค้าสแกน",
        "shop_scan": "ร้านสแกน",
        "phone_entry": "กรอกเบอร์",
        "system": "ค้นชื่อ",
    }

    raw = []
    for p in recent_points:
        raw.append((
            "point", p,
            customers_by_id.get(p.customer_id, "ลูกค้า"),
            method_th_map.get(getattr(p, "issuance_method", ""), "ไม่ระบุ"),
            staff_by_id.get(p.issued_by_staff_id, "—"),
        ))
    for r in recent_redemptions:
        raw.append((
            "redemption", r,
            customers_by_id.get(r.customer_id, "ลูกค้า"),
            "แลกรางวัล",
            staff_by_id.get(r.served_by_staff_id, "—"),
        ))
    raw.sort(key=lambda x: x[1].created_at, reverse=True)

    feed = []
    for kind, item, customer_name, method_th, staff_name in raw:
        # Group same-grant point bursts (or 10s legacy fallback) under
        # one row so a multi-stamp purchase shows as "+5 แต้ม" rather
        # than five identical rows.
        can_group = False
        if (
            feed and kind == "point"
            and feed[-1][0] == "point"
            and feed[-1][2] == customer_name
        ):
            prev_item = feed[-1][1]
            if item.grant_id and prev_item.grant_id == item.grant_id:
                can_group = True
            elif not item.grant_id and not prev_item.grant_id:
                if abs((prev_item.created_at - item.created_at).total_seconds()) < 10:
                    can_group = True
        if can_group:
            feed[-1] = (
                feed[-1][0], feed[-1][1], feed[-1][2],
                feed[-1][3] + 1, feed[-1][4], feed[-1][5],
            )
        else:
            feed.append((kind, item, customer_name, 1, method_th, staff_name))
    return feed[:feed_cap]
