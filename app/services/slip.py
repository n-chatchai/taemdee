"""Slip2Go PromptPay slip verification + topup grant.

Two layers:

1. Low-level Slip2Go HTTP client — `verify_slip_image()` posts a base64
   JPEG to Slip2Go and returns a typed `SlipVerifyResult`. `validate_receiver()`
   checks the parsed response against the shop owner's configured bank
   receiver settings. `get_validated_slip_amount()` returns the THB amount
   when the response indicates success.

2. High-level topup orchestrator — `record_topup()` is the one call routes
   make: image bytes + the package the shop selected → verify → match
   amount → write TopupSlip + CreditLog → grant credits to the shop
   balance. Returns a `TopupResult` with status + a Thai-friendly message
   so the route can render the right outcome page.

Bonus credits are intentionally NOT granted right now — only the package's
base `credits` field hits the balance. Re-enable later by changing
`_credits_for_package()`.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import httpx
from loguru import logger
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models import CreditLog, Shop, TopupSlip
from app.services.deereach import SATANG_PER_CREDIT


# ---------------------------------------------------------------------------
# Slip2Go typed response models — all fields Optional, extra fields ignored
# so any new fields Slip2Go adds in future won't break parsing.
# ---------------------------------------------------------------------------


class _FlexModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Slip2GoBankAccount(_FlexModel):
    account: Optional[str] = None


class Slip2GoProxy(_FlexModel):
    type: Optional[str] = None
    account: Optional[str] = None


class Slip2GoPartyAccount(_FlexModel):
    name: Optional[str] = None
    bank: Optional[Slip2GoBankAccount] = None
    proxy: Optional[Slip2GoProxy] = None


class Slip2GoBank(_FlexModel):
    id: Optional[str] = None
    name: Optional[str] = None


class Slip2GoParty(_FlexModel):
    account: Optional[Slip2GoPartyAccount] = None
    bank: Optional[Slip2GoBank] = None


class Slip2GoData(_FlexModel):
    transRef: Optional[str] = None
    dateTime: Optional[str] = None       # ISO-8601 with tz, kept as str
    amount: Optional[float] = None
    ref1: Optional[str] = None
    ref2: Optional[str] = None
    ref3: Optional[str] = None
    receiver: Optional[Slip2GoParty] = None
    sender: Optional[Slip2GoParty] = None
    decode: Optional[str] = None         # raw QR payload — use for dedup
    referenceId: Optional[str] = None


class Slip2GoResponse(_FlexModel):
    code: Optional[str] = None
    message: Optional[str] = None
    data: Optional[Slip2GoData] = None


class SlipVerifyResult(BaseModel):
    """Internal wrapper around the raw HTTP call. `parsed` is the typed
    view; `response_data` is the raw dict for logging / future-proofing
    against unknown fields."""

    response_data: Optional[dict] = None
    parsed: Optional[Slip2GoResponse] = None
    status_code: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.response_data is not None and self.error is None


# ---------------------------------------------------------------------------
# Low-level Slip2Go client
# ---------------------------------------------------------------------------


_SLIP2GO_URL = "https://connect.slip2go.com/api/verify-slip/qr-base64/info"


async def verify_slip_image(image_bytes: bytes) -> SlipVerifyResult:
    """POST a base64 JPEG to Slip2Go and parse the response.

    Returns a SlipVerifyResult; `ok` is True iff a JSON payload came back.
    Network errors / non-200 / bad JSON populate `.error` instead of raising
    so callers can branch cleanly.
    """
    if not settings.slip2go_api_secret:
        return SlipVerifyResult(error="slip2go_api_secret not configured")

    encoded = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "payload": {
            "imageBase64": f"data:image/jpeg;base64,{encoded}",
            # We dedup ourselves on slip_decode/trans_ref; let the API
            # answer for the same slip multiple times.
            "checkCondition": {"checkDuplicate": False},
        }
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.slip2go_api_secret}",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(_SLIP2GO_URL, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.error("Slip2Go network error: {}", exc)
        return SlipVerifyResult(error=f"network: {exc}")

    if response.status_code != 200:
        return SlipVerifyResult(
            status_code=response.status_code,
            error=response.text[:2000],
        )

    try:
        response_data = response.json()
        parsed = Slip2GoResponse.model_validate(response_data)
    except Exception as exc:
        logger.error("Slip2Go JSON parse error: {}", exc)
        return SlipVerifyResult(
            status_code=response.status_code,
            error=f"parse: {response.text[:1000]}",
        )

    return SlipVerifyResult(
        response_data=response_data,
        parsed=parsed,
        status_code=response.status_code,
    )


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def _csv_tokens(value: Optional[str]) -> list[str]:
    return [t.strip() for t in (value or "").split(",") if t.strip()]


def get_validated_slip_amount(parsed: Slip2GoResponse) -> float:
    """THB amount when the response code/message marks the slip as valid.

    Returns 0 if not a recognized success — caller treats that as a
    verification failure regardless of whether `data.amount` was set.
    """
    code = str(parsed.code or "")
    message = str(parsed.message or "")
    amount = parsed.data.amount if parsed.data else None

    success_codes = set(_csv_tokens(settings.slip2go_success_codes)) or {"200000", "200200"}
    success_markers = _csv_tokens(settings.slip2go_success_messages) or [
        "Slip found", "Slip is valid",
    ]

    if code in success_codes or any(m in message for m in success_markers):
        return float(amount or 0)
    return 0.0


def _account_matches(expected: str, got: str) -> bool:
    """Slip2Go masks middle digits with 'x'; suffix-match or position-wise
    'x' is a wildcard. Empty inputs never match."""
    if not expected or not got:
        return False
    if got.endswith(expected):
        return True
    if len(expected) != len(got):
        return False
    for exp_ch, got_ch in zip(expected.lower(), got.lower()):
        if exp_ch == "x" or got_ch == "x":
            continue
        if exp_ch != got_ch:
            return False
    return True


def validate_receiver(data: Slip2GoData) -> tuple[bool, str]:
    """Verify the slip's receiver matches the shop's configured bank.
    Each check is skipped when its setting is unset, so a partial config
    still works (useful in dev). Returns `(ok, reason)`."""
    receiver = data.receiver
    bank_id = (receiver.bank.id if receiver and receiver.bank else "") or ""
    acct_bank = (
        receiver.account.bank.account
        if receiver and receiver.account and receiver.account.bank
        else ""
    ) or ""
    acct_proxy = (
        receiver.account.proxy.account
        if receiver and receiver.account and receiver.account.proxy
        else ""
    ) or ""
    acct_name = (receiver.account.name if receiver and receiver.account else "") or ""

    if settings.bank_receiver_bank_id:
        if bank_id != settings.bank_receiver_bank_id:
            return False, f"bank_id_mismatch: got '{bank_id}', expected '{settings.bank_receiver_bank_id}'"

    if settings.bank_receiver_account_suffix:
        account_str = acct_bank or acct_proxy
        clean_got = account_str.replace("-", "").replace(" ", "")
        clean_exp = settings.bank_receiver_account_suffix.replace("-", "").replace(" ", "")
        if not _account_matches(clean_exp, clean_got):
            return False, f"account_suffix_mismatch: '{account_str}' does not match"

    if settings.bank_receiver_name:
        receiver_name = acct_name.upper()
        expected_tokens = [t.strip().upper() for t in settings.bank_receiver_name.split(",") if t.strip()]
        if not any(token in receiver_name for token in expected_tokens):
            return False, f"name_mismatch: '{receiver_name}' does not contain any of {expected_tokens}"

    return True, "ok"


# ---------------------------------------------------------------------------
# Topup orchestrator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TopupResult:
    """Outcome of a topup attempt. `kind` drives the route's response page;
    `message` is the Thai-friendly reason to surface to the shop owner."""

    kind: str  # success | wrong_amount | bad_receiver | duplicate | verify_failed | not_configured
    message: str
    credits_granted: int = 0
    amount_thb: int = 0
    trans_ref: Optional[str] = None
    slip_id: Optional[UUID] = None


def _credits_for_package(package: dict) -> int:
    """Credits actually granted on success. Bonus is intentionally
    excluded for now — re-enable by returning credits + bonus."""
    return int(package.get("credits", 0))


def _slip_dedup_hash(parsed: Slip2GoResponse, image_bytes: bytes) -> str:
    """Stable identifier for the slip. Prefer Slip2Go's `decode` (the raw
    QR payload — same physical slip yields the same value); fall back to
    transRef; finally hash the image bytes so re-uploads of the same JPEG
    still dedupe even if the verify call failed."""
    if parsed.data:
        if parsed.data.decode:
            return f"decode:{parsed.data.decode}"
        if parsed.data.transRef:
            return f"ref:{parsed.data.transRef}"
    return f"img:{hashlib.sha256(image_bytes).hexdigest()}"


async def record_topup(
    db: AsyncSession,
    shop: Shop,
    package_key: str,
    package: dict,
    image_bytes: bytes,
    image_url: str,
) -> TopupResult:
    """One-call topup: verify the slip, match the package's price, write
    the TopupSlip + CreditLog, increment shop.credit_balance.

    `image_url` is whatever the route stored the JPEG at (R2 key, local
    path, etc.) — preserved on the TopupSlip row for audit. The slip
    bytes themselves are also hashed for dedup if Slip2Go can't return
    a stable identifier.
    """
    if settings.bank_transfer_skip_check:
        # Dev shortcut — simulate a clean verify so designers can wire
        # the upload UI without a real bank slip.
        return await _grant_topup(
            db,
            shop=shop,
            package_key=package_key,
            package=package,
            amount_thb=int(package["price"]),
            trans_ref=f"SIM-{shop.id}",
            slip_hash=f"sim:{shop.id}:{package_key}",
            image_url=image_url,
        )

    if not settings.slip2go_api_secret:
        return TopupResult(
            kind="not_configured",
            message="ระบบตรวจสลิปอัตโนมัติยังไม่ได้ตั้งค่า · ติดต่อทีมงาน",
        )

    result = await verify_slip_image(image_bytes)
    if not result.ok or not result.parsed:
        return TopupResult(
            kind="verify_failed",
            message="ตรวจสลิปไม่สำเร็จ · ลองส่งใหม่อีกครั้ง",
        )

    parsed = result.parsed
    amount_thb = int(round(get_validated_slip_amount(parsed)))
    if amount_thb <= 0:
        return TopupResult(
            kind="verify_failed",
            message="ตรวจสลิปไม่สำเร็จ · ลองส่งใหม่อีกครั้ง",
        )

    if parsed.data:
        ok, reason = validate_receiver(parsed.data)
        if not ok:
            logger.warning("Slip receiver mismatch for shop {}: {}", shop.id, reason)
            return TopupResult(
                kind="bad_receiver",
                message="สลิปนี้ไม่ได้โอนเข้าบัญชีแต้มดี · กรุณาตรวจสอบและส่งใหม่",
                amount_thb=amount_thb,
            )

    expected_thb = int(package["price"])
    if amount_thb != expected_thb:
        return TopupResult(
            kind="wrong_amount",
            message=f"ยอดในสลิป ฿{amount_thb} ไม่ตรงกับแพ็กเกจ ฿{expected_thb} · เลือกแพ็กเกจให้ตรง",
            amount_thb=amount_thb,
        )

    return await _grant_topup(
        db,
        shop=shop,
        package_key=package_key,
        package=package,
        amount_thb=amount_thb,
        trans_ref=parsed.data.transRef if parsed.data else None,
        slip_hash=_slip_dedup_hash(parsed, image_bytes),
        image_url=image_url,
    )


async def _grant_topup(
    db: AsyncSession,
    *,
    shop: Shop,
    package_key: str,
    package: dict,
    amount_thb: int,
    trans_ref: Optional[str],
    slip_hash: str,
    image_url: str,
) -> TopupResult:
    """Apply the credit grant in one transaction. The `slip_hash` unique
    constraint is the duplicate-slip backstop — IntegrityError → 'duplicate'
    return without touching the balance."""
    credits = _credits_for_package(package)
    satang = credits * SATANG_PER_CREDIT

    slip = TopupSlip(
        shop_id=shop.id,
        amount=amount_thb * 100,  # store in satang
        slip_image_url=image_url,
        slip_hash=slip_hash,
        status="verified",
    )
    db.add(slip)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return TopupResult(
            kind="duplicate",
            message="สลิปนี้เคยใช้งานแล้ว · ส่งสลิปใหม่",
            amount_thb=amount_thb,
            trans_ref=trans_ref,
        )

    shop.credit_balance += satang
    db.add(shop)
    db.add(CreditLog(
        shop_id=shop.id,
        amount=satang,
        reason="topup",
        related_id=slip.id,
    ))
    from app.models.util import utcnow
    slip.verified_at = utcnow()
    db.add(slip)
    await db.commit()
    await db.refresh(slip)

    logger.info(
        "Shop {} topup verified: pkg={} ฿{} → {} credits ({} satang)",
        shop.id, package_key, amount_thb, credits, satang,
    )
    return TopupResult(
        kind="success",
        message=f"เติมเครดิต {credits} เครดิตเรียบร้อย",
        credits_granted=credits,
        amount_thb=amount_thb,
        trans_ref=trans_ref,
        slip_id=slip.id,
    )
