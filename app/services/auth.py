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

from loguru import logger

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
        logger.info(f"\n>>> [OTP {settings.environment}] {phone} → {code}  (expires in {OTP_EXPIRY_MINUTES} min)\n", flush=True)
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


def issue_session_token(
    shop_id: UUID,
    staff_id: Optional[UUID] = None,
    is_owner: bool = False,
) -> str:
    """Sign a JWT for the session cookie.

    Every shop session now carries staff_id (owner is modelled as a
    StaffMember with is_owner=True). The is_owner flag is duplicated
    into the JWT so permission gates can short-circuit without a DB
    round-trip on every request. `role` stays in the payload for
    backwards-compat with any sessions issued before the unification.
    """
    expire = utcnow() + timedelta(days=settings.session_expire_days)
    payload = {
        "shop_id": str(shop_id),
        "staff_id": str(staff_id) if staff_id else None,
        "is_owner": bool(is_owner),
        "role": "owner" if is_owner else ("staff" if staff_id else "owner"),
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_session_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


LIVE_QR_TTL_SECONDS = 15
LIVE_QR_GRACE_SECONDS = 15  # extra grace for slow shutters / network


def issue_live_qr_token(shop_id: UUID) -> str:
    """Sign a 15s JWT for the S3.qr rotating-QR mode. The /scan/<shop_id>
    handler validates this when a `?t=<token>` query is present —
    screenshots of stale QRs hit the expired-token branch."""
    expire = utcnow() + timedelta(seconds=LIVE_QR_TTL_SECONDS + LIVE_QR_GRACE_SECONDS)
    payload = {"shop_id": str(shop_id), "kind": "live_qr", "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_live_qr_token(token: str, expected_shop_id: UUID) -> bool:
    """True iff the token is a valid live_qr JWT for this shop and unexpired."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return (
            payload.get("kind") == "live_qr"
            and payload.get("shop_id") == str(expected_shop_id)
        )
    except JWTError:
        return False


def issue_customer_token(customer_id: UUID) -> str:
    """Long-lived token for the customer's identity cookie (1 year)."""
    expire = utcnow() + timedelta(days=365)
    payload = {"customer_id": str(customer_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_customer_token(token: str) -> Optional[UUID]:
    logger.debug(f"Decoding customer token: {token[:20]}")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        logger.debug(f"Decoded customer token payload: {payload}")
        return UUID(payload["customer_id"])
    except (JWTError, KeyError, ValueError):
        logger.warning("Failed to decode customer token: %s", token[:20])
        return None
