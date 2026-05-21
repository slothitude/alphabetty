from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), default="New Chat")
    space_id = Column(Integer, ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True)
    mode = Column(String(50), default="concise")  # concise, detailed, creative, academic, code
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    messages = relationship("Message", back_populates="conversation", order_by="Message.id")
    space = relationship("Space", back_populates="conversations")

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "mode": self.mode,
            "space_id": self.space_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"))
    parent_id = Column(Integer, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    role = Column(String(20))  # user, assistant, system
    content = Column(Text)
    sources = Column(JSON, default=list)  # [{"title": ..., "url": ..., "snippet": ...}]
    follow_ups = Column(JSON, default=list)  # ["question1", "question2", "question3"]
    created_at = Column(DateTime(timezone=True), default=utcnow)

    conversation = relationship("Conversation", back_populates="messages")

    def to_dict(self):
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "sources": self.sources or [],
            "follow_ups": self.follow_ups or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"))
    index = Column(Integer)  # [1], [2], etc.
    title = Column(String(1000))
    url = Column(String(2000))
    snippet = Column(Text)
    domain = Column(String(500))

    message = relationship("Message")
