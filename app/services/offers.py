"""Offer service — grant + redeem promises (PRD §13).

v1 grants:
  System → Shop: credit_grant (welcome credits, referral bonus, comp)
  Shop → Customer: free_stamp / bonus_stamp_count / free_item

Redeem semantics:
  credit_grant — bumps shop.credit_balance + writes CreditLog (auto-redeemed at grant)
  free_stamp / bonus_stamp_count — issues Stamp rows via services.issuance
  free_item — banner only; no DB-side effect (cashier hands the item over)
"""

from typing import Optional
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import CreditLog, Customer, Offer, Shop, Stamp
from app.models.util import utcnow
from app.services.issuance import issue_stamp


class OfferError(Exception):
    pass


# ----------------------------------------------------------------------
# Grants — create the promise
# ----------------------------------------------------------------------


async def grant_credit_to_shop(
    db: AsyncSession,
    shop: Shop,
    *,
    amount: int,
    description: str = "ระบบให้เครดิต",
    source_shop_id: Optional[UUID] = None,
) -> Offer:
    """System grants credits to a shop. Auto-redeemed (credits land immediately).

    `source_shop_id` set when this came from a Shop→Shop referral payout.
    """
    if amount <= 0:
        raise OfferError("amount must be positive")

    offer = Offer(
        source_type="shop" if source_shop_id else "system",
        source_shop_id=source_shop_id,
        target_type="shop",
        target_shop_id=shop.id,
        kind="credit_grant",
        amount=amount,
        description=description,
        max_uses=1,
        used_count=1,
        status="redeemed",
        last_used_at=utcnow(),
    )
    shop.credit_balance += amount
    db.add(shop)
    db.add(offer)
    db.add(CreditLog(
        shop_id=shop.id,
        amount=amount,
        reason="credit_grant",
        related_id=offer.id,
    ))
    await db.commit()
    await db.refresh(offer)
    return offer


async def grant_offer_to_customer(
    db: AsyncSession,
    shop: Shop,
    customer: Customer,
    *,
    kind: str,
    amount: Optional[int] = None,
    description: Optional[str] = None,
) -> Offer:
    """Shop grants a free_stamp / bonus_stamp_count / free_item to a customer.
    Stays active until redeemed (or expired)."""
    if kind not in ("free_stamp", "bonus_stamp_count", "free_item"):
        raise OfferError(f"Unsupported customer-side kind: {kind}")
    if kind == "bonus_stamp_count" and (amount is None or amount <= 0):
        raise OfferError("bonus_stamp_count requires positive amount")
    if kind == "free_item" and not description:
        raise OfferError("free_item requires description")

    offer = Offer(
        source_type="shop",
        source_shop_id=shop.id,
        target_type="customer",
        target_customer_id=customer.id,
        kind=kind,
        amount=amount,
        description=description,
    )
    db.add(offer)
    await db.commit()
    await db.refresh(offer)
    return offer


# ----------------------------------------------------------------------
# Redemption — apply the offer's effect
# ----------------------------------------------------------------------


async def redeem_offer(db: AsyncSession, offer: Offer) -> Offer:
    """Apply the offer's effect. Idempotent guard: status must be 'active'."""
    if offer.status != "active":
        raise OfferError(f"Offer is not active (status={offer.status})")
    if offer.valid_until and offer.valid_until < utcnow():
        offer.status = "expired"
        db.add(offer)
        await db.commit()
        raise OfferError("Offer expired")

    if offer.kind == "free_stamp":
        if offer.target_type != "customer" or not offer.target_customer_id or not offer.source_shop_id:
            raise OfferError("free_stamp offer missing target customer / source shop")
        shop = await db.get(Shop, offer.source_shop_id)
        customer = await db.get(Customer, offer.target_customer_id)
        await issue_stamp(db, shop, customer, method="system")

    elif offer.kind == "bonus_stamp_count":
        if not offer.amount or offer.target_type != "customer":
            raise OfferError("bonus_stamp_count offer invalid")
        shop = await db.get(Shop, offer.source_shop_id)
        customer = await db.get(Customer, offer.target_customer_id)
        for _ in range(offer.amount):
            await issue_stamp(db, shop, customer, method="system")

    elif offer.kind == "free_item":
        # No DB-side effect — cashier hands the item over IRL.
        pass

    elif offer.kind == "credit_grant":
        # Auto-redeemed at grant time; double-redeem is a bug.
        raise OfferError("credit_grant is auto-redeemed at grant time")

    else:
        raise OfferError(f"Unknown kind: {offer.kind}")

    offer.used_count += 1
    offer.last_used_at = utcnow()
    if offer.used_count >= offer.max_uses:
        offer.status = "redeemed"
    db.add(offer)
    await db.commit()
    await db.refresh(offer)
    return offer


async def list_active_offers_for_customer(
    db: AsyncSession, customer_id: UUID, shop_id: UUID
) -> list[Offer]:
    """Active offers for this customer at this shop — used by DeeCard banner."""
    now = utcnow()
    result = await db.exec(
        select(Offer).where(
            Offer.target_type == "customer",
            Offer.target_customer_id == customer_id,
            Offer.source_shop_id == shop_id,
            Offer.status == "active",
        )
    )
    offers = list(result.all())
    return [o for o in offers if not o.valid_until or o.valid_until >= now]
