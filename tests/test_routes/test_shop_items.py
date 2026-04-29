"""Dashboard claimable items — first kind is welcome_credit."""

from sqlmodel import select

from app.core.config import settings
from app.models import CreditLog, ShopItem


async def test_dashboard_shows_welcome_credit_for_new_shop(auth_client, db, shop):
    shop.is_onboarded = True
    db.add(shop)
    await db.commit()

    r = await auth_client.get("/shop/dashboard")
    assert r.status_code == 200
    body = r.text
    assert "รายการที่ต้องทำ" in body
    assert "รับเครดิตต้อนรับ" in body
    assert f'action="/shop/items/welcome_credit/claim"' in body


async def test_claim_welcome_credit_grants_satang_and_logs(auth_client, db, shop):
    shop.is_onboarded = True
    shop.credit_balance = 0
    db.add(shop)
    await db.commit()

    starting = settings.credit_welcome_amount * 100  # → satang

    r = await auth_client.post("/shop/items/welcome_credit/claim", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/shop/dashboard"

    # Balance bumped by exactly the env amount × 100.
    await db.refresh(shop)
    assert shop.credit_balance == starting

    # CreditLog entry with reason='welcome_credit' for audit.
    logs = (await db.exec(
        select(CreditLog).where(
            CreditLog.shop_id == shop.id,
            CreditLog.reason == "welcome_credit",
        )
    )).all()
    assert len(logs) == 1
    assert logs[0].amount == starting

    # ShopItem row recorded so the dashboard hides the card next time.
    rows = (await db.exec(
        select(ShopItem).where(
            ShopItem.shop_id == shop.id, ShopItem.kind == "welcome_credit"
        )
    )).all()
    assert len(rows) == 1


async def test_claim_welcome_credit_idempotent(auth_client, db, shop):
    """Double-tap → 400 instead of double-grant."""
    shop.is_onboarded = True
    db.add(shop)
    await db.commit()

    r1 = await auth_client.post("/shop/items/welcome_credit/claim", follow_redirects=False)
    assert r1.status_code == 303

    r2 = await auth_client.post("/shop/items/welcome_credit/claim", follow_redirects=False)
    assert r2.status_code == 400
    assert "Already claimed" in r2.json()["detail"]


async def test_dashboard_hides_item_after_claim(auth_client, db, shop):
    shop.is_onboarded = True
    db.add(shop)
    await db.commit()

    await auth_client.post("/shop/items/welcome_credit/claim", follow_redirects=False)

    r = await auth_client.get("/shop/dashboard")
    body = r.text
    assert "รับเครดิตต้อนรับ" not in body


async def test_claim_unknown_kind_400(auth_client, db, shop):
    shop.is_onboarded = True
    db.add(shop)
    await db.commit()

    r = await auth_client.post("/shop/items/telepathy_bonus/claim", follow_redirects=False)
    assert r.status_code == 400
    assert "Unknown item kind" in r.json()["detail"]
