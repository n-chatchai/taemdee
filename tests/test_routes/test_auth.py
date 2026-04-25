from sqlmodel import select

from app.core.auth import SESSION_COOKIE_NAME
from app.models import OtpCode, Shop


async def test_otp_request_creates_code(client, db):
    response = await client.post("/auth/otp/request", data={"phone": "0812345678"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    result = await db.exec(select(OtpCode).where(OtpCode.phone == "0812345678"))
    otp = result.first()
    assert otp is not None
    assert len(otp.code) == 4


async def test_otp_verify_creates_shop_and_session(client, db):
    await client.post("/auth/otp/request", data={"phone": "0812345678"})
    result = await db.exec(select(OtpCode).where(OtpCode.phone == "0812345678"))
    otp = result.first()

    response = await client.post(
        "/auth/otp/verify",
        data={"phone": "0812345678", "code": otp.code, "name": "New Cafe"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert SESSION_COOKIE_NAME in response.cookies

    result = await db.exec(select(Shop).where(Shop.phone == "0812345678"))
    shop = result.first()
    assert shop is not None
    assert shop.name == "New Cafe"


async def test_otp_verify_bad_code_400(client):
    response = await client.post(
        "/auth/otp/verify",
        data={"phone": "0812345678", "code": "0000", "name": "X"},
    )
    assert response.status_code == 400


async def test_logout_returns_ok(auth_client):
    response = await auth_client.post("/auth/logout")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
