"""DeeReach — suggestion engine + send pipeline.

This module owns the logic that decides what DeeReach campaign cards to show
on the shop dashboard. The shop owner taps Send on a suggestion → that triggers
`send_campaign`.

Unit convention:  1 Credit == 100 satang.
Channel costs (satang):
  line     → 100 satang  (1 Cr — primary channel for v1)
  sms      → 300 satang  (3 Cr — fallback, R6c)
  web_push →  50 satang  (0.5 Cr — PWA push, R6c)
  inbox    →   0 satang  (free — DeeCard in-app)

Send flow (Lock → Enqueue → Return, zero UI latency):
  1. Compute audience + estimate cost in satang.
  2. Lock (deduct) credits from shop.credit_balance.
  3. Create DeeReachCampaign (status=locked) + one DeeReachMessage per recipient.
  4. Enqueue an RQ job (app.tasks.deereach.run_deereach_campaign).
  5. Return the campaign immediately — dispatcher runs in background.
  6. RQ task reconciles: refunds failed-message satang back to balance.

R6b: LINE / SMS / web-push stubs live in app/tasks/deereach.py.
R6c: replace stub bodies with real API calls there.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import UUID

from sqlmodel import and_, func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.redis_queue import task_queue
from app.models import CreditLog, Customer, CustomerShopMute, DeeReachCampaign, Redemption, Shop, Point, User
from app.models.deereach import DeeReachMessage
from app.models.util import utcnow

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Satang cost constants  (1 Credit == 100 satang)
# ---------------------------------------------------------------------------
SATANG_PER_CREDIT = 100

# DeeReach paid channels — single source of truth for the user-facing
# list (Thai label, per-message cost in credits, enabled flag) and the
# satang ledger that drives _pick_channel below. Keys match the
# internal channel names emitted by _pick_channel and consumed by
# tasks/deereach.py's send dispatcher; the `label` is what shop
# owners see on /shop/topup and /shop/deereach. `enabled=False`
# dark-launches a channel (e.g. SMS off until DLT/sender approval).
DEEREACH_CHANNELS: dict[str, dict] = {
    "web_push": {"label": "แต้มดี", "cost_credits": 0.5, "enabled": True},
    "line":     {"label": "ไลน์",   "cost_credits": 1.0, "enabled": True},
    "sms":      {"label": "SMS",    "cost_credits": 2.0, "enabled": True},
}

# Per-channel cost in satang — derived from DEEREACH_CHANNELS so a
# price tweak only happens in one place. `inbox` (in-app, always
# reachable, free) is the fallback when no paid channel works and
# isn't in DEEREACH_CHANNELS by design — it's not a billable option
# the owner picks, just the floor.
CHANNEL_COST_SATANG: dict[str, int] = {
    key: int(cfg["cost_credits"] * SATANG_PER_CREDIT)
    for key, cfg in DEEREACH_CHANNELS.items()
}
CHANNEL_COST_SATANG["inbox"] = 0


def sends_remaining_per_channel(credit_balance_satang: int) -> dict[str, int]:
    """How many messages of each enabled channel `credit_balance_satang`
    would buy, ignoring everything else. Used by the /shop/topup hero
    to surface the same number broken down per channel."""
    credits = credit_balance_satang / SATANG_PER_CREDIT
    return {
        key: int(credits / cfg["cost_credits"]) if cfg["cost_credits"] > 0 else 0
        for key, cfg in DEEREACH_CHANNELS.items()
        if cfg["enabled"]
    }

# Win-back: customer's last stamp at this shop is between MIN and MAX days ago.
# Design says "14+ วัน" — bump min to 14 and widen the upper bound to a year so
# we don't silently drop customers who have been gone longer.
WIN_BACK_DAYS_MIN = 14
WIN_BACK_DAYS_MAX = 365

# Almost-there: customer is within N stamps of reward AND last stamp recent.
ALMOST_THERE_GAP_MAX = 2
ALMOST_THERE_LAST_VISIT_DAYS = 14

# Unredeemed reward: card is at goal but customer hasn't claimed in K days.
UNREDEEMED_DAYS_MIN = 7

# New-customer: first stamp at this shop within the last K days. Encourages
# the shop to thank brand-new visitors and convert them into regulars.
NEW_CUSTOMER_DAYS = 7


@dataclass
class Suggestion:
    """One DeeReach suggestion card on the shop dashboard."""

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
        .join(User, Customer.user_id == User.id)
        .where(
            last_stamp.c.last_at >= earliest_last_stamp,
            last_stamp.c.last_at <= latest_last_stamp,
            User.line_id.is_not(None),
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
        .join(User, Customer.user_id == User.id)
        .where(
            and_(
                active.c.active_count >= min_active,
                active.c.active_count < shop.reward_threshold,
                active.c.last_at >= recent_cutoff,
                User.line_id.is_not(None),
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
        .join(User, Customer.user_id == User.id)
        .where(
            active.c.active_count >= shop.reward_threshold,
            active.c.last_at <= cutoff,
            User.line_id.is_not(None),
        )
    )
    result = await db.exec(stmt)
    return list(result.all())


async def find_new_customers(
    db: AsyncSession,
    shop: Shop,
    days: int = NEW_CUSTOMER_DAYS,
) -> List[Customer]:
    """Customers whose FIRST stamp at this shop landed within the last `days`
    days — they're new to *this* shop (could still be regulars elsewhere).
    Send a thank-you to convert them into repeat visitors."""
    now = utcnow()
    cutoff = now - timedelta(days=days)

    first_stamp = (
        select(
            Point.customer_id.label("cid"),
            func.min(Point.created_at).label("first_at"),
        )
        .where(Point.shop_id == shop.id, Point.is_voided == False)  # noqa: E712
        .group_by(Point.customer_id)
        .subquery()
    )

    stmt = (
        select(Customer)
        .join(first_stamp, first_stamp.c.cid == Customer.id)
        .join(User, Customer.user_id == User.id)
        .where(
            first_stamp.c.first_at >= cutoff,
            User.line_id.is_not(None),
        )
    )
    result = await db.exec(stmt)
    return list(result.all())


async def find_all_reachable_customers(
    db: AsyncSession,
    shop: Shop,
) -> List[Customer]:
    """Every customer who has ever stamped at this shop, ordered by most-
    recent activity first. Used by the kind='manual' editor — the owner
    pick recipients themselves so we don't apply a kind-specific filter.
    Anonymous customers are kept too (they fall through to the inbox
    channel, which is free and always succeeds)."""
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
        .order_by(last_stamp.c.last_at.desc())
    )
    return list((await db.exec(stmt)).all())


# ----------------------------------------------------------------------
# Suggestion composition
# ----------------------------------------------------------------------


def _line_cost(audience_count: int) -> int:
    """Estimate display cost in Credits for suggestion cards.

    Uses LINE channel cost as the baseline (cheapest paid channel).
    The actual per-recipient cost is determined at send time via _pick_channel.
    Returns whole credits (rounded up) so the UI never shows a fraction.
    """
    satang = audience_count * CHANNEL_COST_SATANG["line"]
    return -(-satang // SATANG_PER_CREDIT)  # ceiling division


async def compute_suggestions(
    db: AsyncSession,
    shop: Shop,
    *,
    max_suggestions: int = 4,
) -> List[Suggestion]:
    """Returns up to N DeeReach suggestions ranked by impact / freshness.

    A suggestion is *only included* if its audience is non-empty.
    """
    out: List[Suggestion] = []

    # Per PRD §10 anti-spam — exclude customers already messaged this shop
    # in the last 14 days from every kind. Mute (CustomerShopMute) is also
    # applied so an opted-out customer never inflates the suggestion
    # audience count.
    rate_limited = await _recently_messaged_customer_ids(db, shop)
    muted = await _muted_customer_ids(db, shop)

    def _eligible(customers: List[Customer]) -> List[Customer]:
        return [c for c in customers if c.id not in rate_limited and c.id not in muted]

    # 1. Unredeemed reward — highest urgency (customer earned it, just forgot)
    unredeemed = _eligible(await find_unredeemed_reward_customers(db, shop))
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
    almost = _eligible(await find_almost_there_customers(db, shop))
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
    lapsed = _eligible(await find_lapsed_customers(db, shop))
    if lapsed:
        out.append(
            Suggestion(
                kind="win_back",
                label="ชวนกลับ",
                head=f"ชวน {len(lapsed)} คนที่หายไปกลับมา?",
                body=f"พวกเขาหายไป {WIN_BACK_DAYS_MIN}+ วัน",
                audience_count=len(lapsed),
                cost_credit=_line_cost(len(lapsed)),
            )
        )

    # 4. New customers — say thank-you to first-timers within the last K days
    new_customers = _eligible(await find_new_customers(db, shop))
    if new_customers:
        out.append(
            Suggestion(
                kind="new_customer",
                label="ขอบคุณลูกค้าใหม่",
                head=f"ขอบคุณ {len(new_customers)} คนที่มาครั้งแรกสัปดาห์นี้",
                body=f"พวกเขาแวะมาครั้งแรกใน {NEW_CUSTOMER_DAYS} วันที่ผ่านมา",
                audience_count=len(new_customers),
                cost_credit=_line_cost(len(new_customers)),
            )
        )

    return out[:max_suggestions]


# ---------------------------------------------------------------------------
# Send pipeline (Lock → Enqueue → Return)
# ---------------------------------------------------------------------------


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
    if kind == "new_customer":
        return f"ขอบคุณที่แวะมา {shop.name} นะ — แวะอีกครั้งครบ {shop.reward_threshold} แต้ม รับ {shop.reward_description}"
    if kind == "manual":
        # Empty seed — owner is writing their own copy. The textarea
        # placeholder + send-disabled-when-blank guard handles the UX.
        return ""
    return f"ทักทายจาก {shop.name}"


# Per PRD §10 anti-spam: a single shop must not message the same customer
# more than once every RATE_LIMIT_DAYS days, regardless of kind. Protects
# customers from hammering and the platform from blocked LINE OAs.
RATE_LIMIT_DAYS = 14


async def _recently_messaged_customer_ids(
    db: AsyncSession, shop: Shop, days: int = RATE_LIMIT_DAYS,
) -> set[UUID]:
    """Customer ids that already received a DeeReach msg from this shop
    within the last `days` days. Excluded from new audiences."""
    cutoff = utcnow() - timedelta(days=days)
    rows = (await db.exec(
        select(DeeReachMessage.customer_id)
        .join(DeeReachCampaign, DeeReachCampaign.id == DeeReachMessage.campaign_id)
        .where(
            DeeReachCampaign.shop_id == shop.id,
            DeeReachMessage.created_at >= cutoff,
        )
        .distinct()
    )).all()
    return set(rows)


async def _muted_customer_ids(db: AsyncSession, shop: Shop) -> set[UUID]:
    """Customer ids that opted out of DeeReach for this specific shop
    (CustomerShopMute row exists). Per PRD §10 anti-spam, mute is
    enforced for every kind including manual — owner explicit-pick can
    skip rate-limit, but a customer's "ไม่อยากรับข้อความ" is the
    customer's choice."""
    rows = (await db.exec(
        select(CustomerShopMute.customer_id)
        .where(CustomerShopMute.shop_id == shop.id)
    )).all()
    return set(rows)


async def _audience_for(db: AsyncSession, shop: Shop, kind: str) -> List[Customer]:
    if kind == "win_back":
        candidates = await find_lapsed_customers(db, shop)
    elif kind == "almost_there":
        candidates = await find_almost_there_customers(db, shop)
    elif kind == "unredeemed_reward":
        candidates = await find_unredeemed_reward_customers(db, shop)
    elif kind == "new_customer":
        candidates = await find_new_customers(db, shop)
    elif kind == "manual":
        # Manual campaigns are owner-composed (explicit human intent +
        # per-recipient toggle in the editor) so they bypass the
        # 14-day platform rate-limit. Auto-fired suggestion kinds keep
        # the limit so the system itself can't spam a customer. Mute
        # still applies — opt-out is the customer's right.
        muted = await _muted_customer_ids(db, shop)
        return [
            c for c in await find_all_reachable_customers(db, shop)
            if c.id not in muted and c.notifications_enabled
        ]
    else:
        raise DeeReachSendError(f"Unsupported kind: {kind}")

    rate_limited = await _recently_messaged_customer_ids(db, shop)
    muted = await _muted_customer_ids(db, shop)
    # `notifications_enabled` is the customer's master kill-switch from
    # settings.notif. Any kind respects it — even manual owner-composed
    # campaigns shouldn't reach a customer who flipped it off.
    return [
        c for c in candidates
        if c.id not in rate_limited
        and c.id not in muted
        and c.notifications_enabled
    ]


def _line_reachable(customer: Customer) -> bool:
    """LINE Messaging API can only push to users who've added the
    @taemdee OA as a friend. NULL friend_status is treated as "maybe"
    so a first send still gets attempted (and the dispatcher flips
    status to 'unfollowed' on a 403). Explicit 'unfollowed' is the
    permanent skip signal — customer has either tapped block or LINE
    returned 403 in a prior campaign.
    """
    if not customer.line_id:
        return False
    return customer.line_friend_status != "unfollowed"


def _pick_channel(customer: Customer) -> str:
    """Waterfall: use customer's preferred channel, else pick cheapest available.

    Per PRD §10 the waterfall favours the cheapest reachable channel:
    web_push (0.5 Cr) > line (1 Cr) > sms (3 Cr) > inbox (0 Cr fallback).
    `inbox` is always available (DB write only — no external API call).
    `line` is only reachable when the customer has friended the OA — see
    _line_reachable.
    """
    pref = customer.preferred_channel
    if pref == "line" and not _line_reachable(customer):
        # Honour the customer's preference up to a hard reachability
        # gate — explicit unfollow shouldn't be silently re-attempted.
        pref = None
    if pref in CHANNEL_COST_SATANG:
        return pref
    # Waterfall fallback — cheapest first.
    if customer.web_push_endpoint:
        return "web_push"
    if _line_reachable(customer):
        return "line"
    if customer.phone:
        return "sms"
    return "inbox"


def _estimate_cost_satang(audience: List[Customer]) -> int:
    """Sum of per-recipient costs in satang (used for the Lock step)."""
    return sum(CHANNEL_COST_SATANG[_pick_channel(c)] for c in audience)


async def send_campaign(
    db: AsyncSession,
    shop: Shop,
    kind: str,
    *,
    message_override: Optional[str] = None,
    selected_customer_ids: Optional[set] = None,
    offer_kind: Optional[str] = None,
    offer_label: Optional[str] = None,
    offer_image: Optional[str] = None,
    offer_amount: Optional[int] = None,
    offer_unit: Optional[str] = None,
    offer_starts_at: Optional[datetime] = None,
    offer_expires_at: Optional[datetime] = None,
) -> DeeReachCampaign:
    """Lock → Enqueue → Return.  Zero UI latency — actual delivery is async.

    Steps:
      1. Compute audience + estimate cost in satang.
      2. Guard: empty audience or insufficient credit_balance.
      3. Lock credits: deduct from shop.credit_balance, write CreditLog.
      4. Create DeeReachCampaign (status="locked") + DeeReachMessage per recipient.
      5. Commit everything atomically.
      6. Enqueue RQ job — worker reconciles and refunds failed messages.

    `message_override` lets the editor surface (S13.detail) ship custom
    copy. When None we fall back to the per-kind template via
    `render_message`. Empty/whitespace-only is rejected as
    DeeReachSendError so a stray "ส่ง" tap with a blank textarea doesn't
    burn credits on a blank message.

    `selected_customer_ids` lets the editor narrow the audience by
    deselecting specific customers. The kind-based eligibility query
    still runs server-side and stays the source of truth — we just
    intersect with the requested set so the client cannot opt anyone
    *into* a campaign they weren't already eligible for. None means
    "send to everyone the audience query returned".

    Raises DeeReachSendError on empty audience, empty selection,
    insufficient credits, or blank override.
    """
    audience = await _audience_for(db, shop, kind)
    if not audience:
        raise DeeReachSendError("ไม่มีผู้รับที่เข้าเงื่อนไข")

    if selected_customer_ids is not None:
        audience = [c for c in audience if c.id in selected_customer_ids]
        if not audience:
            raise DeeReachSendError("ไม่ได้เลือกผู้รับ — แตะเลือกอย่างน้อย 1 คน")

    locked_satang = _estimate_cost_satang(audience)

    if locked_satang > shop.credit_balance:
        shortfall_cr = (locked_satang - shop.credit_balance) / SATANG_PER_CREDIT
        raise DeeReachSendError(
            f"เครดิตไม่พอ — ขาดอีก {shortfall_cr:.1f} เครดิต"
        )

    if message_override is not None:
        message = message_override.strip()
        if not message:
            raise DeeReachSendError("ข้อความว่าง — ใส่ข้อความก่อนส่ง")
    else:
        message = await render_message(kind, shop)

    # ------------------------------------------------------------------
    # Lock credits
    # ------------------------------------------------------------------
    shop.credit_balance -= locked_satang
    db.add(shop)

    db.add(CreditLog(
        shop_id=shop.id,
        amount=-locked_satang,  # negative = deduction (satang)
        reason="deereach_lock",
        # related_id filled after campaign insert below
    ))

    # ------------------------------------------------------------------
    # Create campaign record
    # ------------------------------------------------------------------
    # Trim/normalize the optional offer fields. When offer_label
    # ends up empty (no offer attached or whitespace-only input),
    # NULL out every offer field so a free-form blank doesn't leave
    # half-set discount or icon columns on the row.
    norm_offer_label = (offer_label or "").strip() or None
    norm_offer_image = (offer_image or "").strip() or None
    norm_offer_kind = (offer_kind or "").strip() or None
    norm_offer_unit = (offer_unit or "").strip() or None
    has_offer = norm_offer_label is not None
    campaign = DeeReachCampaign(
        shop_id=shop.id,
        kind=kind,
        audience_count=len(audience),
        status="locked",
        locked_credits_satang=locked_satang,
        final_credits_satang=0,
        message_text=message,
        offer_kind=norm_offer_kind if has_offer else None,
        offer_label=norm_offer_label,
        offer_image=norm_offer_image if has_offer else None,
        offer_amount=offer_amount if has_offer else None,
        offer_unit=norm_offer_unit if has_offer else None,
        offer_starts_at=offer_starts_at if has_offer else None,
        offer_expires_at=offer_expires_at if has_offer else None,
        sent_at=utcnow(),
    )
    db.add(campaign)

    # Flush so campaign.id is available for foreign keys below
    await db.flush()

    # ------------------------------------------------------------------
    # Create per-recipient DeeReachMessage rows
    # ------------------------------------------------------------------
    for customer in audience:
        channel = _pick_channel(customer)
        db.add(DeeReachMessage(
            campaign_id=campaign.id,
            customer_id=customer.id,
            channel=channel,
            cost_satang=CHANNEL_COST_SATANG[channel],
            status="pending",
        ))

    await db.commit()
    await db.refresh(campaign)

    # ------------------------------------------------------------------
    # Enqueue background job — import here to avoid circular deps
    # ------------------------------------------------------------------
    task_queue.enqueue(
        "app.tasks.deereach.run_deereach_campaign",
        str(campaign.id),
        job_timeout=300,       # 5 min max per campaign
        result_ttl=3600,       # keep result 1 h for debugging
    )

    log.info(
        "Campaign %s enqueued — kind=%s audience=%d locked=%d satang",
        campaign.id, kind, len(audience), locked_satang,
    )
    return campaign
