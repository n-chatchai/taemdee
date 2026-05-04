"""LINE Messaging API — push messages from the platform OA (@taemdee)
to customers who have added it as a friend.

Mechanics:

- Login channel + Messaging channel must share the same LINE Provider
  so customer.line_id (captured during LINE Login) matches the userId
  the Messaging API expects as the push recipient. We rely on this
  invariant — there's no per-shop OA in v1.
- Channel access token is a long-lived bearer credential, set via
  settings.line_oa_channel_access_token. Channel secret is used to
  HMAC-verify webhook bodies.
- Friend gate: a recipient who hasn't followed @taemdee returns
  403 from the API. We surface that to the caller so the campaign
  reconciliation can refund the credit and update the customer's
  line_friend_status to "unfollowed".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

PUSH_URL = "https://api.line.me/v2/bot/message/push"
# Generous timeout — LINE's API is normally <500ms but the worker can
# absorb a slow response without dropping the queue.
_HTTP_TIMEOUT = 8.0


@dataclass
class LineSendResult:
    """Outcome of a single push call.

    `delivered` is what the campaign counter cares about. `friend_gated`
    distinguishes "user hasn't followed @taemdee" from a generic 4xx so
    the caller can flip line_friend_status accordingly.
    """

    delivered: bool
    status_code: int
    friend_gated: bool = False
    detail: Optional[str] = None


def push_text(line_id: str, text: str) -> LineSendResult:
    """Push a single plain-text message to one user. Synchronous (httpx
    sync client) because the only caller is the RQ worker — keeping it
    sync sidesteps the can't-mix-event-loops dance in tasks/deereach.py.
    """
    if not settings.line_messaging_configured:
        # Defensive — _send_line should have already short-circuited.
        log.warning("line push attempted without OA token configured")
        return LineSendResult(delivered=False, status_code=0, detail="not_configured")

    if not line_id:
        return LineSendResult(delivered=False, status_code=0, detail="no_line_id")

    payload = {
        "to": line_id,
        "messages": [{"type": "text", "text": text[:5000]}],  # LINE's max is 5000 chars
    }
    headers = {
        "Authorization": f"Bearer {settings.line_oa_channel_access_token}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(PUSH_URL, json=payload, headers=headers, timeout=_HTTP_TIMEOUT)
    except httpx.HTTPError as e:
        log.warning("line push network error → line_id=%s: %s", line_id, e)
        return LineSendResult(delivered=False, status_code=0, detail=f"network: {e}")

    # 200 = accepted by LINE. 403 with this body shape = "the user hasn't
    # added the bot as a friend" — exactly the case we want to mark.
    if resp.status_code == 200:
        return LineSendResult(delivered=True, status_code=200)

    # LINE error envelope: {"message": "...", "details": [...]}.
    body = resp.text[:500]
    friend_gated = resp.status_code == 403
    log.warning(
        "line push failed → line_id=%s status=%s body=%r",
        line_id, resp.status_code, body,
    )
    return LineSendResult(
        delivered=False,
        status_code=resp.status_code,
        friend_gated=friend_gated,
        detail=body,
    )


def verify_signature(body: bytes, signature_header: Optional[str]) -> bool:
    """Verify the X-Line-Signature header LINE attaches to webhook
    deliveries. HMAC-SHA256(channel_secret, body), base64-encoded.
    Returns False on missing header or mismatch — never raises so the
    webhook handler can return 401 cleanly.
    """
    if not signature_header or not settings.line_oa_channel_secret:
        return False
    expected = base64.b64encode(
        hmac.new(
            settings.line_oa_channel_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, signature_header)
