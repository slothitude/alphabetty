"""Share model — expiring read-only links to conversations."""

from datetime import datetime, timezone, timedelta

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from .conversation import Base


def _default_token():
    import secrets
    return secrets.token_urlsafe(24)


class Share(Base):
    __tablename__ = "shares"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String(64), unique=True, default=_default_token, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"))
    created_by = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    conversation = relationship("Conversation", lazy="joined")
