"""DeeReach — suggestion engine + send pipeline.

This module owns the logic that decides what DeeReach campaign cards to show
on the DeeBoard. The shop owner taps Send on a suggestion → that triggers
`send_campaign`.

Pricing (PRD §11): 1 Credit per LINE message, 3 Credit per SMS. v1 only
counts customers reachable via LINE (line_id != NULL). SMS fallback in R6c.

R6b: send is logged but not actually delivered to LINE — the LINE Messaging
API integration lands in R6c once a real LINE Official Account is provisioned.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional
from uuid import UUID

from sqlmodel import and_, func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import CreditLog, Customer, DeeReachCampaign, Redemption, Shop, Point
from app.models.util import utcnow

log = logging.getLogger(__name__)

CREDIT_PER_LINE = 1
CREDIT_PER_SMS = 3

# Win-back: customer's last stamp at this shop is between MIN and MAX days ago.
WIN_BACK_DAYS_MIN = 30
WIN_BACK_DAYS_MAX = 90

# Almost-there: customer is within N stamps of reward AND last stamp recent.
ALMOST_THERE_GAP_MAX = 2
ALMOST_THERE_LAST_VISIT_DAYS = 14

# Unredeemed reward: card is at goal but customer hasn't claimed in K days.
UNREDEEMED_DAYS_MIN = 7


@dataclass
class Suggestion:
    """One DeeReach suggestion card on the DeeBoard."""

    kind: str               # win_back | almost_there | unredeemed_reward | ...
    label: str              # ชวนกลับ · ใกล้ครบ · รับรางวัลซะที
    head: str               # main headline
    body: str               # one-line description
    audience_count: int
    cost_credit: int

    @property
    def affordable_for(self) -> int:
        """How many recipients the cost covers (used when balance is partial)."""
        return self.audience_count if self.cost_credit > 0 else 0


# ----------------------------------------------------------------------
# Audience queries
# ----------------------------------------------------------------------


async def find_lapsed_customers(
    db: AsyncSession,
    shop: Shop,
    days_min: int = WIN_BACK_DAYS_MIN,
    days_max: int = WIN_BACK_DAYS_MAX,
) -> List[Customer]:
    """Customers whose last stamp at this shop was between days_min and days_max
    days ago AND who have a LINE id (we can actually reach them)."""
    now = utcnow()
    earliest_last_stamp = now - timedelta(days=days_max)  # before this = too lapsed
    latest_last_stamp = now - timedelta(days=days_min)    # after this = still active

    last_stamp = (
        select(
            Point.customer_id.label("cid"),
            func.max(Point.created_at).label("last_at"),
        )
        .where(Point.shop_id == shop.id, Point.is_voided == False)  # noqa: E712
        .group_by(Point.customer_id)
        .subquery()
    )

    stmt = (
        select(Customer)
        .join(last_stamp, last_stamp.c.cid == Customer.id)
        .where(
            last_stamp.c.last_at >= earliest_last_stamp,
            last_stamp.c.last_at <= latest_last_stamp,
            Customer.line_id.is_not(None),
        )
    )
    result = await db.exec(stmt)
    return list(result.all())


async def find_almost_there_customers(
    db: AsyncSession,
    shop: Shop,
    gap_max: int = ALMOST_THERE_GAP_MAX,
    last_visit_days: int = ALMOST_THERE_LAST_VISIT_DAYS,
) -> List[Customer]:
    """Customers with active stamps in [threshold - gap_max, threshold - 1] AND
    a stamp in the last `last_visit_days` days (so we don't ping those who fell off)."""
    now = utcnow()
    recent_cutoff = now - timedelta(days=last_visit_days)
    min_active = max(1, shop.reward_threshold - gap_max)

    # Active stamps per customer (matches services/redemption._active_stamp_where).
    active = (
        select(
            Point.customer_id.label("cid"),
            func.count().label("active_count"),
            func.max(Point.created_at).label("last_at"),
        )
        .where(
            Point.shop_id == shop.id,
            Point.is_voided == False,  # noqa: E712
            or_(
                Point.redemption_id.is_(None),
                Point.redemption_id.in_(
                    select(Redemption.id).where(Redemption.is_voided == True)  # noqa: E712
                ),
            ),
        )
        .group_by(Point.customer_id)
        .subquery()
    )

    stmt = (
        select(Customer)
        .join(active, active.c.cid == Customer.id)
        .where(
            and_(
                active.c.active_count >= min_active,
                active.c.active_count < shop.reward_threshold,
                active.c.last_at >= recent_cutoff,
                Customer.line_id.is_not(None),
            )
        )
    )
    result = await db.exec(stmt)
    return list(result.all())


async def find_unredeemed_reward_customers(
    db: AsyncSession,
    shop: Shop,
    days_min: int = UNREDEEMED_DAYS_MIN,
) -> List[Customer]:
    """Customers whose active card is at-or-above goal but they haven't claimed
    for at least `days_min` days. They probably forgot — nudge them."""
    now = utcnow()
    cutoff = now - timedelta(days=days_min)

    active = (
        select(
            Point.customer_id.label("cid"),
            func.count().label("active_count"),
            func.max(Point.created_at).label("last_at"),
        )
        .where(
            Point.shop_id == shop.id,
            Point.is_voided == False,  # noqa: E712
            or_(
                Point.redemption_id.is_(None),
                Point.redemption_id.in_(
                    select(Redemption.id).where(Redemption.is_voided == True)  # noqa: E712
                ),
            ),
        )
        .group_by(Point.customer_id)
        .subquery()
    )

    stmt = (
        select(Customer)
        .join(active, active.c.cid == Customer.id)
        .where(
            active.c.active_count >= shop.reward_threshold,
            active.c.last_at <= cutoff,
            Customer.line_id.is_not(None),
        )
    )
    result = await db.exec(stmt)
    return list(result.all())


# ----------------------------------------------------------------------
# Suggestion composition
# ----------------------------------------------------------------------


def _line_cost(audience_count: int) -> int:
    return audience_count * CREDIT_PER_LINE


async def compute_suggestions(
    db: AsyncSession,
    shop: Shop,
    *,
    max_suggestions: int = 3,
) -> List[Suggestion]:
    """Returns up to N DeeReach suggestions ranked by impact / freshness.

    A suggestion is *only included* if its audience is non-empty.
    """
    out: List[Suggestion] = []

    # 1. Unredeemed reward — highest urgency (customer earned it, just forgot)
    unredeemed = await find_unredeemed_reward_customers(db, shop)
    if unredeemed:
        out.append(
            Suggestion(
                kind="unredeemed_reward",
                label="รับรางวัลซะที",
                head=f"เตือน {len(unredeemed)} คนที่ยังไม่รับรางวัล",
                body=f"พวกเขาครบ {shop.reward_threshold} แต้มแล้วแต่ยังไม่ได้รับ",
                audience_count=len(unredeemed),
                cost_credit=_line_cost(len(unredeemed)),
            )
        )

    # 2. Almost-there — short nudge, often converts
    almost = await find_almost_there_customers(db, shop)
    if almost:
        out.append(
            Suggestion(
                kind="almost_there",
                label="ใกล้ครบ",
                head=f"ส่งกำลังใจให้ {len(almost)} คนที่ใกล้ครบ?",
                body=f"พวกเขาเหลืออีก 1–{ALMOST_THERE_GAP_MAX} แต้มเท่านั้น",
                audience_count=len(almost),
                cost_credit=_line_cost(len(almost)),
            )
        )

    # 3. Win-back — broader net, lapsed regulars
    lapsed = await find_lapsed_customers(db, shop)
    if lapsed:
        out.append(
            Suggestion(
                kind="win_back",
                label="ชวนกลับ",
                head=f"ชวน {len(lapsed)} คนที่หายไปกลับมา?",
                body=f"พวกเขาหายไป {WIN_BACK_DAYS_MIN}–{WIN_BACK_DAYS_MAX} วัน",
                audience_count=len(lapsed),
                cost_credit=_line_cost(len(lapsed)),
            )
        )

    return out[:max_suggestions]


# ----------------------------------------------------------------------
# Send (stub — R6b will wire LINE Messaging API)
# ----------------------------------------------------------------------


class DeeReachSendError(Exception):
    pass


async def render_message(kind: str, shop: Shop) -> str:
    """Default Thai copy per kind. v2: shop can override per campaign."""
    if kind == "win_back":
        return f"คิดถึงคุณนะ! กลับมาแวะ {shop.name} ได้เลย แต้มยังเก็บไว้ให้"
    if kind == "almost_there":
        return f"ใกล้ครบแล้ว! เก็บอีกนิดเดียวรับ {shop.reward_description} ที่ {shop.name}"
    if kind == "unredeemed_reward":
        return f"คุณมี {shop.reward_description} รออยู่ที่ {shop.name} — แวะมารับได้เลย"
    return f"ทักทายจาก {shop.name}"


async def _audience_for(db: AsyncSession, shop: Shop, kind: str) -> List[Customer]:
    if kind == "win_back":
        return await find_lapsed_customers(db, shop)
    if kind == "almost_there":
        return await find_almost_there_customers(db, shop)
    if kind == "unredeemed_reward":
        return await find_unredeemed_reward_customers(db, shop)
    raise DeeReachSendError(f"Unsupported kind: {kind}")


async def send_campaign(
    db: AsyncSession,
    shop: Shop,
    kind: str,
) -> DeeReachCampaign:
    """Compute audience for `kind`, charge credits, record campaign + log entry.

    R6b stub: messages are logged via stdout/journal — no LINE API call yet.
    Replace `_dispatch` body in R6c with real Messaging API push.

    Raises DeeReachSendError on empty audience or insufficient credits.
    Atomic: credit deduction + CreditLog + Campaign all commit together.
    """
    audience = await _audience_for(db, shop, kind)
    if not audience:
        raise DeeReachSendError("ไม่มีผู้รับที่เข้าเงื่อนไข")

    cost = _line_cost(len(audience))
    if cost > shop.credit_balance:
        raise DeeReachSendError(
            f"เครดิตไม่พอ — ต้องการ {cost}, มี {shop.credit_balance}"
        )

    message = await render_message(kind, shop)
    sent_at = utcnow()

    # In R6b this is a stub — real LINE Messaging API push lives in R6c.
    await _dispatch(audience, message)

    campaign = DeeReachCampaign(
        shop_id=shop.id,
        kind=kind,
        audience_count=len(audience),
        credits_spent=cost,
        message_text=message,
        sent_at=sent_at,
    )
    db.add(campaign)

    shop.credit_balance -= cost
    db.add(shop)

    db.add(CreditLog(
        shop_id=shop.id,
        amount=-cost,
        reason="deereach_send",
    ))

    await db.commit()
    await db.refresh(campaign)
    return campaign


async def _dispatch(audience: List[Customer], message: str) -> None:
    """R6b stub: log the recipients + message. R6c: real LINE Messaging API push."""
    log.info(
        "deereach send (STUB) recipients=%d msg=%r",
        len(audience),
        message,
    )
    for c in audience:
        log.info("  → would push to LINE id=%s", c.line_id)
