"""External-platform webhooks. Currently: LINE Messaging API events.

The LINE Console points each Messaging channel at one HTTPS endpoint
that receives an `events[]` batch — follow / unfollow / message / etc.
We only act on follow/unfollow today; everything else is logged and
ignored. The body is HMAC-signed with the channel secret; reject any
request whose X-Line-Signature doesn't verify.

Mounted at /webhooks/* from main.py — outside the subdomain-routing
allow-list those paths bypass on principle, since LINE doesn't know
about shop.* vs main domain. Add a registered route in the LINE
Console: https://taemdee.com/webhooks/line
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.models import User
from app.models.util import utcnow
from app.services.line_messaging import verify_signature

router = APIRouter()
log = logging.getLogger(__name__)


@router.post("/line")
async def line_webhook(
    request: Request,
    x_line_signature: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Receive LINE webhook events. We care about:

    - `follow`     : customer added the OA → mark line_friend_status='friended'
    - `unfollow`   : customer removed the OA or blocked it → mark 'unfollowed'

    Everything else is logged at debug and acknowledged with 200 so
    LINE's delivery retries stop.
    """
    body = await request.body()
    if not verify_signature(body, x_line_signature):
        log.warning("line webhook signature verify failed")
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    # LINE sends a JSON envelope: {"destination": "...", "events": [...]}
    import json
    try:
        envelope = json.loads(body)
    except json.JSONDecodeError:
        log.warning("line webhook bad json")
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    events = envelope.get("events") or []
    for ev in events:
        ev_type = ev.get("type")
        source = ev.get("source") or {}
        line_id = source.get("userId")
        if not line_id:
            log.debug("line webhook event without userId: %s", ev_type)
            continue

        if ev_type == "follow":
            await _set_friend_status(db, line_id, "friended", clear_blocked=True)
            log.info("line follow → line_id=%s", line_id)
        elif ev_type == "unfollow":
            await _set_friend_status(db, line_id, "unfollowed", set_blocked=True)
            log.info("line unfollow → line_id=%s", line_id)
        else:
            log.debug("line webhook ignored event type=%s", ev_type)

    return Response(status_code=status.HTTP_200_OK)


async def _set_friend_status(
    db: AsyncSession,
    line_id: str,
    status_value: str,
    *,
    clear_blocked: bool = False,
    set_blocked: bool = False,
) -> None:
    """Lookup the User by LINE userId and update their friend gate.

    Users who've never logged in via LINE (so line_id never captured)
    won't match — that's fine, the next time they log in they'll be
    flagged via the OAuth callback's identity claim and the next
    webhook hit will catch up.
    """
    result = await db.exec(select(User).where(User.line_id == line_id))
    user = result.first()
    if user is None:
        log.debug("line webhook: no user row for line_id=%s yet", line_id)
        return
    user.line_friend_status = status_value
    if clear_blocked:
        user.line_messaging_blocked_at = None
    if set_blocked:
        user.line_messaging_blocked_at = utcnow()
    db.add(user)
    await db.commit()
