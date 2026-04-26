"""PDPA service: account delete + anonymous-profile expiry purge."""

from datetime import timedelta

from sqlmodel import select

from app.models import Customer, Point
from app.models.util import utcnow
from app.services.pdpa import (
    delete_customer_account,
    find_inactive_anonymous_customers,
)


async def test_delete_scrubs_pii_keeps_stamps(db, shop):
    c = Customer(is_anonymous=False, line_id="U_keepme", phone="0811", display_name="Ann")
    db.add(c)
    await db.commit()
    await db.refresh(c)
    db.add(Point(shop_id=shop.id, customer_id=c.id, issuance_method="customer_scan"))
    await db.commit()

    await delete_customer_account(db, c)

    await db.refresh(c)
    assert c.line_id is None
    assert c.phone is None
    assert c.display_name is None
    assert c.is_anonymous is True

    stamps = (await db.exec(select(Point).where(Point.customer_id == c.id))).all()
    assert len(stamps) == 1


async def test_inactive_anonymous_finder(db, shop):
    """Anonymous customers whose latest stamp is >365 days old → eligible for purge."""
    old = Customer(is_anonymous=True)
    fresh = Customer(is_anonymous=True)
    db.add_all([old, fresh])
    await db.commit()
    await db.refresh(old)
    await db.refresh(fresh)

    db.add(Point(
        shop_id=shop.id,
        customer_id=old.id,
        issuance_method="customer_scan",
        created_at=utcnow() - timedelta(days=400),
    ))
    db.add(Point(
        shop_id=shop.id,
        customer_id=fresh.id,
        issuance_method="customer_scan",
        created_at=utcnow() - timedelta(days=10),
    ))
    await db.commit()

    inactive = await find_inactive_anonymous_customers(db)
    assert {c.id for c in inactive} == {old.id}


# `purge_inactive_anonymous` removed — see note in services/pdpa.py.
# The `find_inactive_anonymous_customers` test above guards the query that
# whichever cleanup-policy lands on top of will eventually use.
