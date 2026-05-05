"""Customer dashboard items — todo cards on /my-cards.

Mirror of services/items.py for the customer side. Two ways an item
gets hidden:

  1. Customer tapped claim or skip → CustomerItem row exists for this
     (customer, kind). Row is the durable signal.
  2. Live `is_fulfilled(customer)` predicate evaluates True — the
     customer already did the underlying thing without going through
     the todo (e.g. they installed the PWA before we showed the card,
     or they friended @taemdee via the inline banner). The predicate
     is stateless and re-evaluated on every render.

Most claim handlers are no-ops: the act of visiting the linked page
or reaching the underlying state IS the completion criterion. The
backup-recovery flow is the exception — its claim handler doesn't
exist yet (the route that lets the customer see / download the code
hits the auto-fulfill path on its own).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models import Customer, CustomerItem

log = logging.getLogger(__name__)


class ItemError(Exception):
    """Raised when an item can't be claimed (gated, already claimed, etc.)."""


@dataclass
class CustomerDashboardItem:
    kind: str
    label: str        # short title — supports inline <strong>
    sub: str          # one-line description
    cta: str          # button label
    link: Optional[str] = None
    skip_explain: str = "ปิดได้ตอนนี้ · ทำได้ภายหลังในตั้งค่า"
    # Predicate: when True, item is treated as already done and never
    # rendered. Stateless — pass the Customer row in.
    is_fulfilled: Callable[[Customer], bool] = lambda c: False
    # Predicate: when False, item is gated (don't show to this customer
    # at this moment). Different from is_fulfilled — gating means the
    # underlying action isn't applicable yet (e.g. backup-recovery only
    # makes sense for anonymous customers with a code on file). Default
    # is "always show".
    is_eligible: Callable[[Customer], bool] = lambda c: True
    scan_count: int = 2 # How many times the customer scanned their card before this item appears?


def _has_any_provider(c: Customer) -> bool:
    return bool(c.line_id or c.google_id or c.facebook_id or c.phone)


def _line_messaging_enabled() -> bool:
    return settings.line_messaging_configured


# Registry. Order = display order on /my-cards.
ITEMS: list[CustomerDashboardItem] = [
    CustomerDashboardItem(
        kind="pwa_install",
        label="<strong>เพิ่มลงหน้าจอ</strong>",
        sub="เปิดแต้มดีจากหน้าจอ · ไม่ต้องเปิดเบราว์เซอร์",
        cta="เพิ่มเลย →",
        # No `link` — user opens via the existing install_sheet trigger;
        # auto-fulfills when /track/pwa flips customer.is_pwa to True.
        is_fulfilled=lambda c: bool(c.is_pwa),
        skip_explain="ใช้ผ่านเบราว์เซอร์ก็ได้ · เพิ่มได้ทุกเมื่อ",
    ),
    CustomerDashboardItem(
        kind="connect_provider",
        label="<strong>ผูกบัญชี</strong>เก็บแต้มไว้ไม่ให้หาย",
        sub="LINE / Google · กู้คืนแต้มได้ถ้าเปลี่ยนเครื่อง",
        cta="ผูกที่นี่ →",
        link="/card/account",
        # Anonymous customer with no provider linked yet.
        is_eligible=lambda c: c.is_anonymous,
        is_fulfilled=lambda c: _has_any_provider(c),
        skip_explain="ใช้แบบไม่ผูกบัญชีก็ได้ · แต้มจะอยู่บนเครื่องนี้",
    ),
    CustomerDashboardItem(
        kind="line_friend",
        label="เพิ่ม <strong>แต้มดี</strong> เป็นเพื่อน",
        sub="แจ้งเตือนของฝาก รับข่าวร้านโปรดผ่านไลน์แต้มดี",
        cta="เพิ่มเพื่อน →",
        # link injected at render time from settings.line_oa_friend_url
        is_eligible=lambda c: bool(c.line_id) and _line_messaging_enabled(),
        is_fulfilled=lambda c: c.line_friend_status == "friended",
        # Skip = "I already added it" OR "I don't use LINE". Either
        # way the row hides; the auto-fulfill predicate catches the
        # actual follow event when the webhook later confirms.
        skip_explain="เพิ่มแล้ว · หรือไม่ใช้ LINE",
    ),
    CustomerDashboardItem(
        kind="enable_push",
        label="<strong>เปิดแจ้งเตือนแต้มดี</strong>",
        sub="แจ้งเตือนของฝาก รับข่าวร้านโปรดผ่านแต้มดี",
        cta="เปิดเลย →",
        # No `link` — frontend wires the tap to navigator pushManager.
        is_fulfilled=lambda c: bool(c.web_push_endpoint),
        skip_explain="ปิดอยู่ · ยังรับข้อความผ่านกล่องข้อความได้",
    ),
    CustomerDashboardItem(
        kind="set_picture",
        label="<strong>ตั้งรูปโปรไฟล์</strong>",
        sub="ปรับแต่งบัญชีให้เป็นตัวเอง ให้ร้านรู้จักคุณได้ง่ายขึ้น",
        cta="เลือกรูป →",
        link="/card/account",
        is_fulfilled=lambda c: bool(c.picture_url),
        skip_explain="ใช้รูปเริ่มต้นก็ได้ · เปลี่ยนได้ภายหลัง",
    ),
]


def _reachable_channel_count(c: Customer) -> int:
    """How many DeeReach channels could reach this customer right now.
    Used to gate the set_preferred_channel todo so we don't ask
    customers with only one option to "pick" anything.
    """
    return sum([
        bool(c.web_push_endpoint),
        bool(c.line_id) and c.line_friend_status != "unfollowed",
        bool(c.phone),
        # Inbox always works, but it's the fallback — don't count it
        # toward "do we need a picker?".
    ])


_KNOWN_KINDS = {it.kind for it in ITEMS}


async def list_available(
    db: AsyncSession, customer: Customer,
) -> list[CustomerDashboardItem]:
    """Items still actionable for this customer.

    Excludes:
      - items the customer claimed/skipped (CustomerItem row exists)
      - items not eligible right now (is_eligible(customer) → False)
      - items already fulfilled (is_fulfilled(customer) → True)
      - items gated behind more scans than the customer has yet
        (it.scan_count > customer's lifetime point count)

    The fulfillment check happens AFTER the eligibility check so an
    ineligible-but-fulfilled state still skips the row.
    """
    from sqlmodel import func
    from app.models import Point

    claimed = (await db.exec(
        select(CustomerItem.kind).where(CustomerItem.customer_id == customer.id)
    )).all()
    claimed_set = set(claimed)

    # Lifetime scan count — non-voided Point rows for this customer.
    # Used to drip-feed todos so brand-new customers aren't buried
    # in 6 things to do on their first stamp.
    scan_count = (await db.exec(
        select(func.count())
        .select_from(Point)
        .where(
            Point.customer_id == customer.id,
            Point.is_voided == False,  # noqa: E712
        )
    )).one()

    out: list[CustomerDashboardItem] = []
    for it in ITEMS:
        if it.kind in claimed_set:
            continue
        if it.scan_count > scan_count:
            continue
        if not it.is_eligible(customer):
            continue
        if it.is_fulfilled(customer):
            continue
        out.append(it)
    return out


async def skip(
    db: AsyncSession, customer: Customer, kind: str,
) -> CustomerItem:
    """Dismiss without running a side effect. Idempotent: a second skip
    raises ItemError so the route can return 400.
    """
    if kind not in _KNOWN_KINDS:
        raise ItemError(f"Unknown item kind: {kind}")
    row = CustomerItem(customer_id=customer.id, kind=kind)
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ItemError(f"Already dismissed: {kind}")
    await db.commit()
    await db.refresh(row)
    log.info("Customer %s skipped item %s", customer.id, kind)
    return row


async def claim(
    db: AsyncSession, customer: Customer, kind: str,
) -> CustomerItem:
    """Mark the item claimed. Most kinds are no-op handlers — the act
    of visiting the linked page is the completion criterion (clicking
    "เซฟรหัสกู้คืน" routes to /recover where the customer reads it,
    and we record the claim either way). Handler hooks are reserved
    for future kinds that need a real side-effect.
    """
    if kind not in _KNOWN_KINDS:
        raise ItemError(f"Unknown item kind: {kind}")
    row = CustomerItem(customer_id=customer.id, kind=kind)
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ItemError(f"Already claimed: {kind}")
    await db.commit()
    await db.refresh(row)
    log.info("Customer %s claimed item %s", customer.id, kind)
    return row
