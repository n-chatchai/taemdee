from app.models import Customer, Point
from app.services.soft_wall import (
    claim_by_facebook,
    claim_by_google,
    claim_by_line,
    claim_by_phone,
)
from tests._helpers import make_customer


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
    existing = await make_customer(db, phone="0877777777", display_name="Prev")

    result = await claim_by_phone(db, customer, phone="0877777777")

    # The anonymous customer's stamps moved to the existing row; the row
    # itself stays in the DB with merged_into_id set so stale cookies can
    # follow the merge chain (see Customer.merged_into_id docstring).
    assert result.id == existing.id
    anon_after = await db.get(Customer, customer.id)
    assert anon_after is not None
    assert anon_after.merged_into_id == existing.id

    from sqlmodel import select
    stamps = (await db.exec(select(Point).where(Point.customer_id == existing.id))).all()
    assert len(stamps) == 1


async def test_claim_already_claimed_is_noop(db):
    claimed = await make_customer(db, phone="0811112222")
    result = await claim_by_phone(db, claimed, phone="0811112222")
    assert result is claimed  # short-circuit, no merge


async def test_claim_by_line_works(db, customer):
    result = await claim_by_line(db, customer, line_id="U1234567")
    assert result.is_anonymous is False
    assert result.line_id == "U1234567"


async def test_claim_by_google_works(db, customer):
    result = await claim_by_google(
        db, customer, google_id="118273645900112233445", display_name="Sarah"
    )
    assert result.is_anonymous is False
    assert result.google_id == "118273645900112233445"
    assert result.display_name == "Sarah"


async def test_claim_by_facebook_works(db, customer):
    result = await claim_by_facebook(
        db, customer, facebook_id="100000123456789", display_name="Bob"
    )
    assert result.is_anonymous is False
    assert result.facebook_id == "100000123456789"
    assert result.display_name == "Bob"


async def test_claim_by_google_merges_existing(db, shop, customer):
    """Existing customer with this google_id absorbs the anonymous one,
    same as the phone/line merge paths."""
    db.add(Point(shop_id=shop.id, customer_id=customer.id, issuance_method="customer_scan"))
    existing = await make_customer(
        db, google_id="118273645900112233445", display_name="Prev",
    )

    result = await claim_by_google(db, customer, google_id="118273645900112233445")
    assert result.id == existing.id
    anon_after = await db.get(Customer, customer.id)
    assert anon_after is not None
    assert anon_after.merged_into_id == existing.id

    from sqlmodel import select
    stamps = (await db.exec(select(Point).where(Point.customer_id == existing.id))).all()
    assert len(stamps) == 1
