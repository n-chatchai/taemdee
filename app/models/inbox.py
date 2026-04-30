from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

from app.models.util import utcnow


class Inbox(SQLModel, table=True):
    """In-app DeeCard inbox row. Per PRD §10, when no push channel can
    reach a customer (no LINE id, no phone, no web push subscription —
    or shop is muted), the DeeReach message silently lands here. The
    customer sees an unread badge in their DeeCard inbox tab and reads
    on their own time. Always succeeds — pure DB write."""

    __tablename__ = "inboxes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    customer_id: UUID = Field(foreign_key="customers.id", index=True)
    shop_id: UUID = Field(foreign_key="shops.id", index=True)
    campaign_id: Optional[UUID] = Field(
        default=None, foreign_key="deereach_campaigns.id", index=True
    )

    body: str

    # Optional "ของฝาก" attached to the message (Inbox.detail offer card).
    # offer_text is the headline ("ลด ฿20 เมื่อซื้อกาแฟ" / "ครัวซองต์ฟรี
    # 1 ชิ้น"). offer_until is the expiry shown as "ใช้ก่อน <date>" — both
    # nullable, when offer_text is empty the card is hidden entirely.
    # Claim flow + Inbox.voucher full-screen QR are deferred until the
    # shop-side S13 editor can write these fields.
    offer_text: Optional[str] = Field(default=None)
    offer_until: Optional[datetime] = Field(default=None)

    read_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, index=True)
