from app.models import Customer, Point
from app.services.soft_wall import claim_by_line, claim_by_phone


async def test_claim_promotes_anonymous_in_place(db, customer):
    """No existing claimed customer: the anonymous row is promoted."""
    anon_id = customer.id
    result = await claim_by_phone(db, customer, phone="0888888888", display_name="Ann")
    assert result.id == anon_id
    assert result.is_anonymous is False
    assert result.phone == "0888888888"
    assert result.display_name == "Ann"


async def test_claim_merges_into_existing(db, shop, customer):
    """Existing claimed customer with same phone absorbs the anonymous one."""
    # Anonymous customer has a stamp at `shop`
    db.add(Point(shop_id=shop.id, customer_id=customer.id, issuance_method="customer_scan"))
    await db.commit()

    # A claimed customer already exists with this phone
    existing = Customer(is_anonymous=False, phone="0877777777", display_name="Prev")
    db.add(existing)
    await db.commit()
    await db.refresh(existing)

    result = await claim_by_phone(db, customer, phone="0877777777")

    # The anonymous customer was deleted; stamps moved to the existing row
    assert result.id == existing.id
    anon_gone = await db.get(Customer, customer.id)
    assert anon_gone is None

    from sqlmodel import select
    stamps = (await db.exec(select(Point).where(Point.customer_id == existing.id))).all()
    assert len(stamps) == 1


async def test_claim_already_claimed_is_noop(db):
    claimed = Customer(is_anonymous=False, phone="0811112222")
    db.add(claimed)
    await db.commit()
    await db.refresh(claimed)

    result = await claim_by_phone(db, claimed, phone="0811112222")
    assert result is claimed  # short-circuit, no merge


async def test_claim_by_line_works(db, customer):
    result = await claim_by_line(db, customer, line_id="U1234567")
    assert result.is_anonymous is False
    assert result.line_id == "U1234567"
