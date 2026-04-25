"""Offer service tests — grant + redeem semantics."""

import pytest
from sqlmodel import select

from app.models import CreditLog, Offer, Stamp
from app.services.offers import (
    OfferError,
    grant_credit_to_shop,
    grant_offer_to_customer,
    list_active_offers_for_customer,
    redeem_offer,
)


async def test_grant_credit_immediately_redeems_and_logs(db, shop):
    initial = shop.credit_balance
    offer = await grant_credit_to_shop(db, shop, amount=100, description="Welcome")
    assert offer.kind == "credit_grant"
    assert offer.status == "redeemed"
    assert offer.amount == 100

    await db.refresh(shop)
    assert shop.credit_balance == initial + 100

    rows = (await db.exec(
        select(CreditLog).where(CreditLog.shop_id == shop.id, CreditLog.reason == "credit_grant")
    )).all()
    assert len(rows) == 1
    assert rows[0].amount == 100


async def test_grant_credit_rejects_zero_or_negative(db, shop):
    with pytest.raises(OfferError):
        await grant_credit_to_shop(db, shop, amount=0)
    with pytest.raises(OfferError):
        await grant_credit_to_shop(db, shop, amount=-5)


async def test_grant_free_stamp_to_customer(db, shop, customer):
    offer = await grant_offer_to_customer(db, shop, customer, kind="free_stamp")
    assert offer.kind == "free_stamp"
    assert offer.status == "active"
    assert offer.target_customer_id == customer.id


async def test_redeem_free_stamp_creates_a_stamp(db, shop, customer):
    offer = await grant_offer_to_customer(db, shop, customer, kind="free_stamp")
    await redeem_offer(db, offer)

    await db.refresh(offer)
    assert offer.status == "redeemed"

    stamps = (await db.exec(
        select(Stamp).where(Stamp.shop_id == shop.id, Stamp.customer_id == customer.id)
    )).all()
    assert len(stamps) == 1
    assert stamps[0].issuance_method == "system"


async def test_redeem_bonus_stamp_count_creates_n(db, shop, customer):
    offer = await grant_offer_to_customer(db, shop, customer, kind="bonus_stamp_count", amount=3)
    await redeem_offer(db, offer)

    stamps = (await db.exec(
        select(Stamp).where(Stamp.customer_id == customer.id)
    )).all()
    assert len(stamps) == 3


async def test_redeem_inactive_offer_raises(db, shop, customer):
    offer = await grant_offer_to_customer(db, shop, customer, kind="free_stamp")
    await redeem_offer(db, offer)
    with pytest.raises(OfferError, match="not active"):
        await redeem_offer(db, offer)


async def test_list_active_offers_for_customer(db, shop, customer):
    await grant_offer_to_customer(db, shop, customer, kind="free_stamp")
    await grant_offer_to_customer(db, shop, customer, kind="free_item", description="Free pastry")
    offers = await list_active_offers_for_customer(db, customer.id, shop.id)
    assert {o.kind for o in offers} == {"free_stamp", "free_item"}


async def test_grant_free_item_requires_description(db, shop, customer):
    with pytest.raises(OfferError, match="description"):
        await grant_offer_to_customer(db, shop, customer, kind="free_item")
