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

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Channel cost table (satang)
# ---------------------------------------------------------------------------
CHANNEL_COST_SATANG: dict[str, int] = {
    "web_push": 50,
    "line": 100,
    "sms": 300,
    "inbox": 0,
}


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


def _send_line(line_id: Optional[str], message: str) -> bool:
    """Stub: succeeds if the customer has a LINE id. R6c: LINE Messaging API."""
    if not line_id:
        log.warning("line STUB → no line_id, marking failed")
        return False
    log.info("line STUB → line_id=%s msg=%r", line_id, message[:40])
    return True


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
        return _send_line(customer.line_id, message)
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

        for msg in messages:
            # Load the customer for channel-specific data (line_id, phone)
            customer = await db.get(Customer, msg.customer_id)
            if customer is None:
                log.warning("Customer %s not found — marking message failed", msg.customer_id)
                msg.status = "failed"
                failed_satang += msg.cost_satang
                db.add(msg)
                continue

            # Get the message text from the campaign record
            message_text = campaign.message_text or ""

            success = await _dispatch_channel(
                msg.channel, customer, message_text,
                db=db, shop_id=shop.id, campaign_id=campaign.id,
            )

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

    await engine.dispose()
