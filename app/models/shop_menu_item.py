from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

from app.models.util import utcnow


class ShopMenuItem(SQLModel, table=True):
    """A "เมนูเด็ด" entry the shop owner adds in /shop/settings/menu and
    that customers see on the shop.story (.ss-menu-grid) tile.

    No image upload yet — the design renders the food icon as a single
    emoji over a tinted gradient background that's cycled by position
    in the grid. Owner picks emoji from a small set in the form.
    `is_signature` surfaces the "ขายดีที่สุด" mint tag overlay.
    `sort_order` controls grid position (lower = earlier).
    """

    __tablename__ = "shop_menu_items"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    shop_id: UUID = Field(foreign_key="shops.id", index=True)
    name: str
    # Baht-only for MVP — Thai SMEs invariably price whole-baht. Nullable
    # so an owner can publish an item before deciding the price.
    price: Optional[int] = Field(default=None)
    emoji: Optional[str] = Field(default=None)
    is_signature: bool = Field(default=False)
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow)
