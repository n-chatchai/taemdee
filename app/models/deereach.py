from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

from app.models.util import utcnow


class DeeReachCampaign(SQLModel, table=True):
    """A DeeReach send — recorded when the shop owner taps "Send" on a suggestion.

    `audience_count` is the snapshot at send time. `credits_spent` is what was
    deducted from the shop's balance. Per-recipient delivery records are not
    stored in v1 (added later if attribution data is needed).
    """

    __tablename__ = "deereach_campaigns"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    shop_id: UUID = Field(foreign_key="shops.id", index=True)

    # Discriminator for the suggestion engine.
    # Valid: win_back, almost_there, unredeemed_reward, birthday, new_product, manual.
    kind: str

    audience_count: int
    status: str = Field(default="pending")  # pending, locked, completed
    locked_credits_satang: int = Field(default=0)
    final_credits_satang: int = Field(default=0)

    # Rendered message body sent to recipients (keeps a record for support).
    message_text: Optional[str] = Field(default=None)

    # Optional attached "ของฝาก" — campaign carries a coupon-style offer
    # rendered as a styled card inside the customer's inbox message.
    # Staff verifies use manually at the shop; no separate Voucher
    # entity in v1.
    #
    # offer_kind selects the design's two flavours:
    #   'free_item' → owner types a label, picks an icon
    #                 (e.g. "กาแฟ Signature ฟรี 1 แก้ว")
    #   'discount'  → owner picks amount + unit
    #                 (offer_label rendered as "ลด 50 บาท" / "ลด 10%")
    #
    # offer_label is the composed display string for both kinds; the
    # dispatcher copies it onto Inbox.offer_text so customer-side
    # rendering doesn't need to know about types.
    offer_kind: Optional[str] = Field(default=None)
    offer_label: Optional[str] = Field(default=None)
    offer_image: Optional[str] = Field(default=None)
    offer_amount: Optional[int] = Field(default=None)  # discount only
    offer_unit: Optional[str] = Field(default=None)    # 'baht' | 'percent'
    offer_starts_at: Optional[datetime] = Field(default=None)
    offer_expires_at: Optional[datetime] = Field(default=None)

    # Set by the owner when they tap Send. Until then this row may not exist —
    # we only persist Campaign records when actually sending.
    sent_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow)


class DeeReachMessage(SQLModel, table=True):
    __tablename__ = "deereach_messages"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    campaign_id: UUID = Field(foreign_key="deereach_campaigns.id", index=True)
    customer_id: UUID = Field(foreign_key="customers.id")
    channel: str  # web_push, line, sms, inbox
    cost_satang: int
    status: str = Field(default="pending")  # pending, delivered, failed
    created_at: datetime = Field(default_factory=utcnow)
