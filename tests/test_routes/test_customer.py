from uuid import uuid4

from sqlmodel import select

from app.models import Stamp


async def test_scan_creates_stamp_and_redirects(client, shop):
    response = await client.get(f"/scan/{shop.id}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/card/{shop.id}"


async def test_scan_persists_stamp(client, db, shop):
    await client.get(f"/scan/{shop.id}", follow_redirects=False)
    result = await db.exec(select(Stamp).where(Stamp.shop_id == shop.id))
    stamps = list(result.all())
    assert len(stamps) == 1
    assert stamps[0].issuance_method == "customer_scan"


async def test_scan_twice_silently_blocks_second(client, db, shop):
    await client.get(f"/scan/{shop.id}", follow_redirects=False)
    response = await client.get(f"/scan/{shop.id}", follow_redirects=False)
    # Daily cap is silent — still returns redirect (lands on card showing current state)
    assert response.status_code == 303
    result = await db.exec(select(Stamp).where(Stamp.shop_id == shop.id))
    assert len(list(result.all())) == 1


async def test_card_renders(client, shop):
    await client.get(f"/scan/{shop.id}", follow_redirects=False)
    response = await client.get(f"/card/{shop.id}")
    assert response.status_code == 200
    assert shop.name in response.text


async def test_scan_unknown_shop_404(client):
    response = await client.get(f"/scan/{uuid4()}", follow_redirects=False)
    assert response.status_code == 404


async def test_card_unknown_shop_404(client):
    response = await client.get(f"/card/{uuid4()}")
    assert response.status_code == 404


async def test_claim_phone_with_valid_otp(client, db, shop):
    # Get an OTP issued
    await client.post("/auth/otp/request", data={"phone": "0833333333"})
    from app.models import OtpCode
    result = await db.exec(select(OtpCode).where(OtpCode.phone == "0833333333"))
    otp = result.first()

    # Visit card first so the customer cookie is set
    await client.get(f"/scan/{shop.id}", follow_redirects=False)

    response = await client.post(
        "/card/claim/phone",
        data={"phone": "0833333333", "code": otp.code, "display_name": "Ann"},
    )
    assert response.status_code == 200
    assert response.json()["claimed"] is True
