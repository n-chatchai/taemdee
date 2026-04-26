"""PDPA — customer-driven account deletion + anonymous-profile expiry.

Per PRD §12: customer can self-delete; their stamps stay (counts preserved
in shop reporting) but identifiers are scrubbed. Anonymous profiles auto-
expire after 12 months of inactivity (run via cron / scheduled job).
"""

from datetime import timedelta
from typing import Sequence
from uuid import UUID

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Customer, Point
from app.models.util import utcnow

ANONYMOUS_INACTIVITY_DAYS = 365


async def delete_customer_account(db: AsyncSession, customer: Customer) -> Customer:
    """Customer-initiated PDPA right-to-delete.

    Scrubs identifying info (phone, LINE id, display_name) but keeps the
    Customer row + their Stamps so the shop's headline count stays accurate.
    The Customer becomes a permanent "anonymous" record.
    """
    customer.line_id = None
    customer.phone = None
    customer.display_name = None
    customer.is_anonymous = True
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return customer


async def find_inactive_anonymous_customers(
    db: AsyncSession,
    inactivity_days: int = ANONYMOUS_INACTIVITY_DAYS,
) -> Sequence[Customer]:
    """Anonymous customers whose latest stamp is older than `inactivity_days`,
    OR who have never had a stamp and were created longer ago than that."""
    cutoff = utcnow() - timedelta(days=inactivity_days)

    last_stamp = (
        select(Point.customer_id, func.max(Point.created_at).label("last_at"))
        .group_by(Point.customer_id)
        .subquery()
    )

    stmt = (
        select(Customer)
        .outerjoin(last_stamp, last_stamp.c.customer_id == Customer.id)
        .where(
            Customer.is_anonymous == True,  # noqa: E712
            (last_stamp.c.last_at.is_(None) & (Customer.created_at <= cutoff))
            | (last_stamp.c.last_at <= cutoff),
        )
    )
    result = await db.exec(stmt)
    return list(result.all())


# NOTE: actual deletion (`purge_inactive_anonymous`) deferred until we decide
# whether to (a) cascade-delete the stamps, (b) make Point.customer_id nullable
# and orphan, or (c) reassign to a "deleted-customers" placeholder. The
# `find_inactive_anonymous_customers` query above is the input that whichever
# policy ends up doing.
