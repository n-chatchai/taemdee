"""DeeReach Dispatcher — RQ background task.

Called by the RQ worker (`worker.py`) after `send_campaign` enqueues the job.

Flow (matches DEEREACH.md §3):
  1. Load the campaign + all its DeeReachMessage rows.
  2. For each message: call the appropriate channel stub and mark
     status = "delivered" | "failed".
  3. Tally delivered vs failed costs (in satang).
  4. Update campaign:  final_credits_satang = delivered sum.
  5. Refund failed credits back to shop.credit_balance and write a
     CreditLog("deereach_refund") entry.
  6. Set campaign.status = "completed".

Unit convention:  1 Credit == 100 satang.
Channel costs (satang):
  web_push →   50 satang  (0.5 Cr — cheap, best-effort)
  line     →  100 satang  (1 Cr)
  sms      →  300 satang  (3 Cr)
  inbox    →    0 satang  (free, always succeeds)

This module must NOT import anything from `app.core.database` at module
load time — RQ imports it in the worker process which has its own DB engine
configured via `DATABASE_URL`. Use `asyncio.run` + a fresh engine per job.
"""

import asyncio
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.credit import CreditLog
from app.models.customer import Customer
from app.models.deereach import DeeReachCampaign, DeeReachMessage
from app.models.inbox import Inbox
from app.models.shop import Shop
from app.services import events

log = logging.getLogger(__name__)

# Channel cost table — re-exported from services/deereach.py so the
# refund maths stay in lockstep with the lock side. Don't redefine
# here; DEEREACH_CHANNELS in services/deereach.py is the single
# source of truth.
from app.services.deereach import CHANNEL_COST_SATANG  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Channel dispatch stubs
# (R6c: replace each stub body with a real API call)
# ---------------------------------------------------------------------------

def _send_web_push(customer: Customer, message: str) -> bool:
    """Encrypt + sign with VAPID and POST to the customer's push endpoint.
    Returns True on 2xx, False on any error (including 410 Gone — caller
    should clear the dead subscription separately).

    Drops to log-only stub when the worker hasn't loaded VAPID keys
    (ensure_vapid_keys never ran for some reason) so dev / tests pass
    without any DB-side keypair.
    """
    if not (customer.web_push_endpoint and customer.web_push_p256dh and customer.web_push_auth):
        log.warning("web_push → no subscription on customer=%s, marking failed", customer.id)
        return False

    from app.services.web_push import (
        WEB_PUSH_VAPID_SUB,
        get_vapid_private_key,
    )
    private_key = get_vapid_private_key()
    if not private_key:
        log.info("web_push STUB (VAPID not loaded in this worker) → customer=%s", customer.id)
        return True
    try:
        import json
        from pywebpush import WebPushException, webpush
        webpush(
            subscription_info={
                "endpoint": customer.web_push_endpoint,
                "keys": {
                    "p256dh": customer.web_push_p256dh,
                    "auth": customer.web_push_auth,
                },
            },
            data=json.dumps({
                "title": "แต้มดี",
                "body": message,
                "url": "/my-inbox",
            }),
            vapid_private_key=private_key,
            vapid_claims={"sub": WEB_PUSH_VAPID_SUB},
            ttl=60 * 60 * 24,  # 24h — push services drop messages older than this
        )
        log.info("web_push delivered → customer=%s", customer.id)
        return True
    except WebPushException as e:
        # 404/410 mean the endpoint is dead — caller can clear the
        # subscription on the customer row. We log here and return False
        # so the campaign reconciles to a refund.
        log.warning("web_push failed → customer=%s status=%s", customer.id, getattr(e.response, "status_code", "?"))
        return False
    except Exception as e:  # noqa: BLE001
        log.exception("web_push unexpected error → customer=%s: %s", customer.id, e)
        return False


def _send_line(customer: Customer, message: str) -> bool:
    """Push the campaign message to the customer's LINE via the platform
    OA's Messaging API. Returns True only when LINE accepts the push.

    Falls back to the log-only stub when the OA isn't configured (dev
    without real LINE creds). On a 403 (recipient hasn't followed the
    OA), flips customer.line_friend_status to 'unfollowed' and clears
    line_messaging_blocked_at so DeeReach's reachability gate stops
    counting them as a `line` recipient until they follow again.
    """
    line_id = customer.line_id
    if not line_id:
        log.warning("line → no line_id on customer=%s, marking failed", customer.id)
        return False

    if not settings.line_messaging_configured:
        # Dev without real LINE creds — keep the previous stub behaviour
        # so existing tests + local campaigns still resolve.
        log.info("line STUB (no OA token) → line_id=%s msg=%r", line_id, message[:40])
        return True

    from app.services.line_messaging import push_text
    from app.models.util import utcnow

    result = push_text(line_id, message)
    if result.delivered:
        return True

    if result.friend_gated:
        # Reflect the unfollow on the customer row so the next campaign's
        # reachability filter skips this line_id entirely. Persisted
        # opportunistically — same async-session-from-sync pattern as
        # the rest of this task.
        customer.user.line_friend_status = "unfollowed"
        customer.user.line_messaging_blocked_at = utcnow()
        log.info("line friend-gated → customer=%s, status flipped to unfollowed", customer.id)

    return False


def _send_sms(phone: Optional[str], message: str) -> bool:
    """Stub: succeeds if the customer has a phone number. R6c: ThaiBulkSMS."""
    if not phone:
        log.warning("sms STUB → no phone, marking failed")
        return False
    log.info("sms STUB → phone=%s msg=%r", phone, message[:40])
    return True


async def _send_inbox(
    db: AsyncSession,
    customer_id: UUID,
    shop_id: UUID,
    campaign_id: UUID,
    message: str,
) -> bool:
    """DeeCard in-app inbox — DB write only, always succeeds. Customer
    sees the message next time they open their card; reads on their own
    time via /my-cards inbox tab."""
    db.add(Inbox(
        customer_id=customer_id,
        shop_id=shop_id,
        campaign_id=campaign_id,
        body=message,
    ))
    log.info("inbox → customer=%s msg=%r", customer_id, message[:40])
    return True


def _substitute_placeholders(text: str, customer: Customer, shop: Shop) -> str:
    """Replace template placeholders with concrete values per recipient.

    Supported tokens (matches the chips in /shop/deereach editor):
      {name}        → customer.display_name (fallback "ลูกค้า")
      {points}      → customer's active stamps at this shop (TODO once
                      we wire it; placeholder swap-to-empty for now so
                      it doesn't read as literal "{points}")
      {shop_name}   → shop.name
      {shop_reward} → shop.reward_description

    Owners insert placeholders via /shop/deereach var-chips and the
    dispatcher fills them at send time. Without this, customers used
    to see literal "สวัสดีพี่{name}" in their LINE/inbox message.
    """
    if not text:
        return text
    name = customer.display_name or "ลูกค้า"
    return (
        text
        .replace("{name}", name)
        .replace("{shop_name}", shop.name or "")
        .replace("{shop_reward}", shop.reward_description or "")
        # TODO: {points} requires a query for active-stamp count at this
        # shop. Stripped to empty for now so the literal token doesn't
        # ship to customers.
        .replace("{points}", "")
    )


async def _dispatch_channel(
    channel: str,
    customer: Customer,
    message: str,
    *,
    db: AsyncSession,
    shop_id: UUID,
    campaign_id: UUID,
) -> bool:
    """Route to the correct channel handler. Returns True = delivered."""
    if channel == "web_push":
        return _send_web_push(customer, message)
    if channel == "line":
        return _send_line(customer, message)
    if channel == "sms":
        return _send_sms(customer.phone, message)
    if channel == "inbox":
        return await _send_inbox(db, customer.id, shop_id, campaign_id, message)
    log.error("Unknown channel %r — treating as failed", channel)
    return False


# ---------------------------------------------------------------------------
# Core task (sync entry point called by RQ)
# ---------------------------------------------------------------------------

def run_deereach_campaign(campaign_id: str) -> None:
    """RQ entry point — wraps the async implementation."""
    asyncio.run(_run(UUID(campaign_id)))


async def _run(campaign_id: UUID) -> None:
    """Async implementation: dispatch, reconcile, finalize."""
    # RQ workers are separate processes; create a fresh engine per job so we
    # don't share connection pools across forked processes.
    engine = create_async_engine(settings.database_url, echo=False)

    # Make sure this worker's NOTIFY publisher pool is up before we start
    # firing inbox-update events at the end. start() is idempotent so
    # repeat calls across jobs are free.
    await events.start()

    async with AsyncSession(engine) as db:
        # ------------------------------------------------------------------
        # 1. Load campaign
        # ------------------------------------------------------------------
        campaign = await db.get(DeeReachCampaign, campaign_id)
        if campaign is None:
            log.error("Campaign %s not found — aborting", campaign_id)
            return
        if campaign.status == "completed":
            log.warning("Campaign %s already completed — idempotency guard", campaign_id)
            return

        shop = await db.get(Shop, campaign.shop_id)
        if shop is None:
            log.error("Shop %s not found for campaign %s", campaign.shop_id, campaign_id)
            return

        # ------------------------------------------------------------------
        # 2. Load all per-recipient messages for this campaign
        # ------------------------------------------------------------------
        stmt = select(DeeReachMessage).where(
            DeeReachMessage.campaign_id == campaign_id,
            DeeReachMessage.status == "pending",
        )
        result = await db.exec(stmt)
        messages = list(result.all())

        if not messages:
            log.warning("Campaign %s has no pending messages — completing with zero spend", campaign_id)
            campaign.status = "completed"
            campaign.final_credits_satang = 0
            db.add(campaign)
            await db.commit()
            return

        log.info("Campaign %s: dispatching %d messages", campaign_id, len(messages))

        # ------------------------------------------------------------------
        # 3. Dispatch each message + mark delivered/failed
        # ------------------------------------------------------------------
        delivered_satang = 0
        failed_satang = 0
        # Track every customer that got an inbox row this run — we publish
        # the new unread count to each of them after the commit lands so
        # any open SSE stream updates the dock badge in real time.
        inbox_recipient_ids: set = set()

        for msg in messages:
            # Load the customer for channel-specific data (line_id, phone)
            customer = await db.get(Customer, msg.customer_id)
            if customer is None:
                log.warning("Customer %s not found — marking message failed", msg.customer_id)
                msg.status = "failed"
                failed_satang += msg.cost_satang
                db.add(msg)
                continue

            # Per-recipient template substitution — owner-typed messages
            # may include {name}/{shop_name}/etc. via the var-chip UI.
            # campaign.message_text is the un-substituted template; we
            # render it for each customer here so {name} resolves to
            # the recipient's display_name, not the same literal for all.
            message_text = _substitute_placeholders(
                campaign.message_text or "", customer, shop,
            )

            success = await _dispatch_channel(
                msg.channel, customer, message_text,
                db=db, shop_id=shop.id, campaign_id=campaign.id,
            )

            # Inbox is the source of truth — every campaign message lands
            # there so customers can re-read past notifications (and so the
            # primary push isn't lost if it was missed/dismissed). When
            # the primary channel WAS inbox, _dispatch_channel already wrote
            # the row — don't double-write.
            if msg.channel != "inbox":
                await _send_inbox(
                    db, customer.id, shop.id, campaign.id, message_text,
                )
            inbox_recipient_ids.add(customer.id)

            if success:
                msg.status = "delivered"
                delivered_satang += msg.cost_satang
            else:
                msg.status = "failed"
                failed_satang += msg.cost_satang

            db.add(msg)

        # ------------------------------------------------------------------
        # 4. Finalize campaign
        # ------------------------------------------------------------------
        campaign.final_credits_satang = delivered_satang
        campaign.status = "completed"
        db.add(campaign)

        # ------------------------------------------------------------------
        # 5. Reconcile: refund failed credits back to shop balance
        # ------------------------------------------------------------------
        if failed_satang > 0:
            log.info(
                "Campaign %s: refunding %d satang (%s Cr) for %d failed messages",
                campaign_id,
                failed_satang,
                f"{failed_satang / 100:.2f}",
                sum(1 for m in messages if m.status == "failed"),
            )
            shop.credit_balance += failed_satang
            db.add(shop)

            db.add(CreditLog(
                shop_id=shop.id,
                amount=failed_satang,  # positive = refund
                reason="deereach_refund",
                related_id=campaign_id,
            ))

        log.info(
            "Campaign %s done — delivered=%d satang, failed=%d satang (refunded)",
            campaign_id,
            delivered_satang,
            failed_satang,
        )

        await db.commit()

        # Publish per-recipient inbox-update so any open SSE stream on the
        # customer's device bumps the dock badge live. Awaited (not fire-
        # and-forget) so the NOTIFY actually flushes before the worker's
        # asyncio.run loop tears down at function exit.
        from sqlalchemy import func as _func
        for cid in inbox_recipient_ids:
            unread = (await db.exec(
                select(_func.count())
                .select_from(Inbox)
                .where(Inbox.customer_id == cid, Inbox.read_at.is_(None))
            )).one()
            try:
                await events.publish_customer_async(cid, "inbox-update", str(unread))
            except Exception:
                log.exception("events: failed to publish inbox-update for customer=%s", cid)

    await engine.dispose()
