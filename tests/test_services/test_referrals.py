"""Shop → Shop referral end-to-end."""

from sqlmodel import select

from app.models import Shop
from app.services.referrals import (
    REFERRAL_REWARD_CREDITS,
    complete_referral_for,
    consume_referral_on_signup,
    create_referral_code,
    find_referral_by_code,
)


async def _shop(db, name: str, phone: str) -> Shop:
    s = Shop(name=name, phone=phone)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def test_create_referral_code_idempotent_for_open_one(db, shop):
    a = await create_referral_code(db, shop)
    b = await create_referral_code(db, shop)
    assert a.id == b.id  # same open referral, not a second one


async def test_full_referral_flow_grants_both_parties(db, shop):
    """Referrer makes a code → referee signs up via it → onboards → both rewarded."""
    initial_referrer = shop.credit_balance

    referral = await create_referral_code(db, shop)

    referee = await _shop(db, "Café Tana", "0822222222")
    initial_referee = referee.credit_balance

    found = await find_referral_by_code(db, referral.code)
    assert found is not None
    assert found.id == referral.id

    await consume_referral_on_signup(db, referral, referee)
    await db.refresh(referral)
    assert referral.referee_shop_id == referee.id
    assert referral.completed_at is None  # not completed until onboarding

    completed = await complete_referral_for(db, referee)
    assert completed is not None
    assert completed.completed_at is not None

    await db.refresh(shop)
    await db.refresh(referee)
    assert shop.credit_balance == initial_referrer + REFERRAL_REWARD_CREDITS
    assert referee.credit_balance == initial_referee + REFERRAL_REWARD_CREDITS


async def test_referee_with_no_referral_completes_silently(db, shop):
    """A shop that wasn't referred returns None from complete_referral_for."""
    result = await complete_referral_for(db, shop)
    assert result is None


async def test_referral_complete_only_fires_once(db, shop):
    referral = await create_referral_code(db, shop)
    referee = await _shop(db, "Café Tana", "0822222222")
    await consume_referral_on_signup(db, referral, referee)

    await complete_referral_for(db, referee)
    initial_referrer = shop.credit_balance
    await db.refresh(shop)
    after_first = shop.credit_balance

    # Second call should be a no-op (no second reward)
    second = await complete_referral_for(db, referee)
    assert second is None
    await db.refresh(shop)
    assert shop.credit_balance == after_first  # unchanged
