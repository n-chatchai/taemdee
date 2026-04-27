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
    credits_spent: int = Field(default=0)

    # Rendered message body sent to recipients (keeps a record for support).
    message_text: Optional[str] = Field(default=None)

    # Set by the owner when they tap Send. Until then this row may not exist —
    # we only persist Campaign records when actually sending.
    sent_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow)
