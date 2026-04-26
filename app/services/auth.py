"""OTP generation/verification and JWT session token issuance."""

import logging
import random
from datetime import timedelta
from typing import Optional
from uuid import UUID

from jose import JWTError, jwt
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models import OtpCode
from app.models.util import utcnow

log = logging.getLogger(__name__)

OTP_EXPIRY_MINUTES = 5


async def generate_and_send_otp(db: AsyncSession, phone: str) -> str:
    """Generate a 4-digit OTP, store it, and 'send' it.

    In development the code is printed to stdout. In production this is where a real SMS
    provider would be called (Phase 2).
    """
    code = f"{random.randint(0, 9999):04d}"
    otp = OtpCode(
        phone=phone,
        code=code,
        expires_at=utcnow() + timedelta(minutes=OTP_EXPIRY_MINUTES),
    )
    db.add(otp)
    await db.commit()

    if settings.environment == "production":
        # TODO (Phase 2): integrate SMS provider
        raise NotImplementedError("SMS provider not yet wired for production")
    else:
        # development / test / staging — log to stdout
        print(f"\n>>> [OTP {settings.environment}] {phone} → {code}  (expires in {OTP_EXPIRY_MINUTES} min)\n", flush=True)
        log.info("otp issued env=%s phone=%s code=%s", settings.environment, phone, code)
    return code


async def verify_otp(db: AsyncSession, phone: str, code: str) -> bool:
    """Return True if the code is valid, unexpired, and unconsumed. Marks it consumed."""
    result = await db.exec(
        select(OtpCode)
        .where(OtpCode.phone == phone, OtpCode.code == code, OtpCode.consumed_at.is_(None))
        .order_by(OtpCode.created_at.desc())
    )
    otp = result.first()

    if otp is None:
        return False
    if otp.expires_at < utcnow():
        return False

    otp.consumed_at = utcnow()
    db.add(otp)
    await db.commit()
    return True


def issue_session_token(shop_id: UUID, staff_id: Optional[UUID] = None) -> str:
    """Sign a JWT for the session cookie."""
    expire = utcnow() + timedelta(days=settings.session_expire_days)
    payload = {
        "shop_id": str(shop_id),
        "staff_id": str(staff_id) if staff_id else None,
        "role": "staff" if staff_id else "owner",
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_session_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def issue_customer_token(customer_id: UUID) -> str:
    """Long-lived token for the customer's identity cookie (1 year)."""
    expire = utcnow() + timedelta(days=365)
    payload = {"customer_id": str(customer_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_customer_token(token: str) -> Optional[UUID]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return UUID(payload["customer_id"])
    except (JWTError, KeyError, ValueError):
        return None
