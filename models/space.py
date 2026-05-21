from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from models.conversation import Base, utcnow


class Space(Base):
    __tablename__ = "spaces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200))
    description = Column(String(1000), default="")
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    color = Column(String(7), default="#3b82f6")
    created_at = Column(DateTime(timezone=True), default=utcnow)

    conversations = relationship("Conversation", back_populates="space")
