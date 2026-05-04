from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel, UniqueConstraint

from app.models.util import utcnow


class CustomerItem(SQLModel, table=True):
    """Per-customer, one-time-claimable dashboard items.

    Customer-side mirror of ShopItem. Surfaces on /my-cards as a small
    todo list — install PWA, link a social provider, friend the OA on
    LINE, etc. Row existence means "this customer has already claimed
    or skipped this kind"; items still show until the row is written.

    Some kinds also have an `is_fulfilled(customer)` predicate in
    services/customer_items.py that treats the item as already-done
    based on the live Customer state (e.g. customer.is_pwa, presence
    of line_id) — those get filtered out without writing a row.
    `(customer_id, kind)` is unique so a customer can only claim each
    kind once.
    """

    __tablename__ = "customer_items"
    __table_args__ = (
        UniqueConstraint("customer_id", "kind", name="uq_customer_items_customer_kind"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    customer_id: UUID = Field(foreign_key="customers.id", index=True)
    kind: str
    claimed_at: datetime = Field(default_factory=utcnow)
