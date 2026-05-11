"""Shared test-suite helpers.

Customer identity moved onto a backing User row in the User-backed
refactor — `Customer.user_id` is NOT NULL. Tests used to write
`Customer(line_id=..., phone=..., display_name=...)` directly; that
no longer works since those columns live on User. Use `make_customer`
to create both rows in one go.
"""

from typing import Optional
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Customer, User


async def make_user(
    db: AsyncSession,
    *,
    line_id: Optional[str] = None,
    google_id: Optional[str] = None,
    facebook_id: Optional[str] = None,
    phone: Optional[str] = None,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
    notifications_enabled: bool = True,
    **extra,
) -> User:
    u = User(
        line_id=line_id,
        google_id=google_id,
        facebook_id=facebook_id,
        phone=phone,
        display_name=display_name,
        picture_url=picture_url,
        notifications_enabled=notifications_enabled,
        **extra,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def make_customer(
    db: AsyncSession,
    *,
    line_id: Optional[str] = None,
    google_id: Optional[str] = None,
    facebook_id: Optional[str] = None,
    phone: Optional[str] = None,
    display_name: Optional[str] = None,
    picture_url: Optional[str] = None,
    notifications_enabled: bool = True,
    is_anonymous: Optional[bool] = None,
    user_id: Optional[UUID] = None,
    preferred_channel: Optional[str] = None,
) -> Customer:
    """Create a Customer with a backing User in one shot.

    `is_anonymous` defaults to True when no identity field is set,
    False otherwise — matches the production heuristic.

    Pass `user_id` to attach the customer to an existing User row
    (used by merge tests that share one identity across many
    customer rows).
    """
    if user_id is None:
        u = await make_user(
            db,
            line_id=line_id,
            google_id=google_id,
            facebook_id=facebook_id,
            phone=phone,
            display_name=display_name,
            picture_url=picture_url,
            notifications_enabled=notifications_enabled,
        )
        user_id = u.id
    if is_anonymous is None:
        is_anonymous = not any([
            line_id, google_id, facebook_id, phone, display_name,
        ])
    c = Customer(
        user_id=user_id,
        is_anonymous=is_anonymous,
        preferred_channel=preferred_channel,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c
