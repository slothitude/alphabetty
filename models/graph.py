from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, Float, UniqueConstraint
from sqlalchemy.orm import relationship

from models.conversation import Base


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), unique=True, nullable=False)
    color = Column(String(7), default="#6366f1")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ConversationTag(Base):
    __tablename__ = "conversation_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"))
    tag_id = Column(Integer, ForeignKey("tags.id", ondelete="CASCADE"))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("conversation_id", "tag_id"),)


class Entity(Base):
    """Named entity / topic extracted from conversations."""
    __tablename__ = "entities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(500), nullable=False)
    entity_type = Column(String(50), default="topic")  # topic, person, org, tech, concept
    description = Column(Text, default="")
    mention_count = Column(Integer, default=1)
    first_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("name", "entity_type"),)


class EntityEdge(Base):
    """Relationship between entities — the graph edges."""
    __tablename__ = "entity_edges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, ForeignKey("entities.id", ondelete="CASCADE"))
    target_id = Column(Integer, ForeignKey("entities.id", ondelete="CASCADE"))
    weight = Column(Float, default=1.0)  # how often these entities co-occur
    relationship_type = Column(String(100), default="co_occurrence")
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("source_id", "target_id"),)


class MessageEntity(Base):
    """Link messages to entities they mention."""
    __tablename__ = "message_entities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"))
    entity_id = Column(Integer, ForeignKey("entities.id", ondelete="CASCADE"))
    relevance = Column(Float, default=1.0)


class DomainGraph(Base):
    """Tracks how domains/sources interconnect across conversations."""
    __tablename__ = "domain_graph"

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain_a = Column(String(500), nullable=False)
    domain_b = Column(String(500), nullable=False)
    co_occurrence = Column(Integer, default=1)  # appeared in same conversation
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("domain_a", "domain_b"),)
