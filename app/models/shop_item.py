from datetime import datetime
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel, UniqueConstraint

from app.models.util import utcnow


class ShopItem(SQLModel, table=True):
    """Per-shop, one-time-claimable dashboard items.

    Generic holder for things the shop interacts with from /shop/dashboard:
    welcome credits, onboarding checklists, share-with-friend bonuses, etc.
    The `kind` discriminator is the item's identity in code; row existence
    means 'this shop has already claimed this kind'. Items not yet claimed
    don't have a row and surface on the dashboard until the owner taps.

    `(shop_id, kind)` is unique so a shop can only claim each kind once."""

    __tablename__ = "shop_items"
    __table_args__ = (
        UniqueConstraint("shop_id", "kind", name="uq_shop_items_shop_kind"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    shop_id: UUID = Field(foreign_key="shops.id", index=True)
    kind: str  # "welcome_credit", "first_topup", ... — see services/items.py
    claimed_at: datetime = Field(default_factory=utcnow)
