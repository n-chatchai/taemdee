"""Shop → Shop referral (PRD §14, v1).

Flow:
  1. Owner taps "Refer a shop" in S10 Settings → `create_referral_code` →
     a Referral row with referee_shop_id=NULL and a unique short code.
  2. Owner shares the link `/shop/register?ref=<code>`.
  3. New shop signs up → `consume_referral_on_signup` records the link.
  4. New shop completes onboarding → `complete_referral` issues credit_grant
     Offers to BOTH parties (via services.offers).
"""

import secrets
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Referral, Shop
from app.models.util import utcnow
from app.services.offers import grant_credit_to_shop

# TBD per PRD §14 — concrete numbers to dial as we test.
REFERRAL_REWARD_CREDITS = 100


async def create_referral_code(db: AsyncSession, shop: Shop) -> Referral:
    """Generate (or reuse the latest open) referral code for this shop."""
    open_one = (await db.exec(
        select(Referral).where(
            Referral.referrer_shop_id == shop.id,
            Referral.referee_shop_id.is_(None),
        )
    )).first()
    if open_one:
        return open_one

    referral = Referral(
        referrer_shop_id=shop.id,
        code=secrets.token_urlsafe(8),
    )
    db.add(referral)
    await db.commit()
    await db.refresh(referral)
    return referral


async def find_referral_by_code(db: AsyncSession, code: str) -> Optional[Referral]:
    return (await db.exec(select(Referral).where(Referral.code == code))).first()


async def consume_referral_on_signup(
    db: AsyncSession, referral: Referral, referee_shop: Shop
) -> Referral:
    """Bind a freshly-created shop to a referral code. No reward yet — that
    fires when the referee completes onboarding."""
    if referral.referee_shop_id is not None:
        # Already used by a different shop. Treat as no-op rather than failing
        # the signup.
        return referral
    referral.referee_shop_id = referee_shop.id
    db.add(referral)
    await db.commit()
    await db.refresh(referral)
    return referral


async def complete_referral_for(db: AsyncSession, referee_shop: Shop) -> Optional[Referral]:
    """Called after a referee shop's onboarding is marked done. If a referral
    is bound to this shop and not yet completed, issue the credit_grant Offers
    to both parties and mark complete."""
    referral = (await db.exec(
        select(Referral).where(
            Referral.referee_shop_id == referee_shop.id,
            Referral.completed_at.is_(None),
        )
    )).first()
    if not referral:
        return None

    referrer = await db.get(Shop, referral.referrer_shop_id)
    if not referrer:
        # Referrer shop deleted — silently void the referral
        return None

    await grant_credit_to_shop(
        db, referrer,
        amount=REFERRAL_REWARD_CREDITS,
        description=f"แนะนำเพื่อนได้สำเร็จ ({referee_shop.name})",
        source_shop_id=referee_shop.id,
    )
    await grant_credit_to_shop(
        db, referee_shop,
        amount=REFERRAL_REWARD_CREDITS,
        description=f"สมัครผ่านลิงก์แนะนำจาก {referrer.name}",
        source_shop_id=referrer.id,
    )

    referral.completed_at = utcnow()
    db.add(referral)
    await db.commit()
    await db.refresh(referral)
    return referral
