"""Dashboard items — generic one-time-claimable cards on /shop/dashboard.

The dashboard shows a list of items the shop hasn't claimed yet (e.g.
'welcome credit'). Each item has a fixed `kind` discriminator that maps
to:

  - copy (label, description, CTA text, accent emoji/icon)
  - a `claim()` side-effect that runs server-side when the owner taps

Adding a new item: append to ITEMS, write a `_claim_<kind>` impl, and
the dashboard surfaces it automatically.
"""

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models import CreditLog, Shop, ShopItem
from app.services.deereach import SATANG_PER_CREDIT

log = logging.getLogger(__name__)


class ItemError(Exception):
    """Raised when an item can't be claimed (gated, already claimed, etc.)."""


@dataclass
class DashboardItem:
    kind: str
    label: str       # short title — e.g. 'รับเครดิตต้อนรับ'
    sub: str         # one-line description under the title
    cta: str         # button label — e.g. 'รับเลย'


# Registry of all dashboard item kinds. Order = display order.
# Labels are HTML-safe — the template renders them with `|safe` so the
# inline <strong> highlight on the credit amount survives. Substitute
# {amount} per shop on render.
ITEMS: list[DashboardItem] = [
    DashboardItem(
        kind="welcome_credit",
        label="รับ<strong>เครดิต {amount}</strong>เปิดบัญชี",
        sub="ครั้งเดียว · ส่งดีรีชหาลูกค้าได้ {amount} ครั้ง",
        cta="รับเลย →",
    ),
]


async def list_available(db: AsyncSession, shop: Shop) -> list[DashboardItem]:
    """Items this shop hasn't claimed yet, formatted with shop-specific copy."""
    claimed = (await db.exec(
        select(ShopItem.kind).where(ShopItem.shop_id == shop.id)
    )).all()
    claimed_set = set(claimed)

    out: list[DashboardItem] = []
    for it in ITEMS:
        if it.kind in claimed_set:
            continue
        if it.kind == "welcome_credit" and settings.credit_welcome_amount <= 0:
            continue  # disabled by ops
        # Substitute {amount} for items that need to render the env value.
        amount = settings.credit_welcome_amount
        out.append(DashboardItem(
            kind=it.kind,
            label=it.label.format(amount=amount),
            sub=it.sub.format(amount=amount),
            cta=it.cta,
        ))
    return out


async def claim(db: AsyncSession, shop: Shop, kind: str) -> ShopItem:
    """Run the side-effect for `kind` and record the claim. Idempotent —
    a second call lands on IntegrityError (uq_shop_items_shop_kind),
    which we surface as ItemError so the route can return 409 instead of
    a 500."""
    handler: Optional[Callable[[AsyncSession, Shop], Awaitable[None]]] = _CLAIM_HANDLERS.get(kind)
    if handler is None:
        raise ItemError(f"Unknown item kind: {kind}")

    # Insert the row first so the unique constraint stops a double-claim
    # before any side-effect runs. If the side-effect fails after this,
    # the transaction is rolled back and the row vanishes — claim is
    # atomic, no half-applied state.
    row = ShopItem(shop_id=shop.id, kind=kind)
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ItemError(f"Already claimed: {kind}")

    try:
        await handler(db, shop)
        await db.commit()
        await db.refresh(row)
        log.info("Shop %s claimed dashboard item %s", shop.id, kind)
        return row
    except Exception:
        await db.rollback()
        raise


async def _claim_welcome_credit(db: AsyncSession, shop: Shop) -> None:
    """Add the welcome-credit grant to the shop's balance + write a
    CreditLog entry (reason='welcome_credit') so the deposit is auditable
    next to topups and DeeReach deductions."""
    amount_credits = settings.credit_welcome_amount
    if amount_credits <= 0:
        raise ItemError("Welcome credit grant is disabled (CREDIT_WELCOME_AMOUNT=0)")
    amount_satang = amount_credits * SATANG_PER_CREDIT

    shop.credit_balance += amount_satang
    db.add(shop)
    db.add(CreditLog(
        shop_id=shop.id,
        amount=amount_satang,  # positive = grant
        reason="welcome_credit",
    ))


_CLAIM_HANDLERS: dict[str, Callable[[AsyncSession, Shop], Awaitable[None]]] = {
    "welcome_credit": _claim_welcome_credit,
}
