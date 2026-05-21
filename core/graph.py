"""Local Knowledge Graph — FTS5 search + entity extraction + graph relationships.

- Full-text search across all conversations, messages, and sources
- Auto-extract topics/entities from messages using LLM
- Build graph: entity-entity, domain-domain, conversation-entity edges
- Query the graph: "what do I know about X?" → related entities, conversations, sources
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import select, text, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app import async_session, engine
from config import settings
from models.conversation import Conversation, Message
from models.graph import Tag, ConversationTag, Entity, EntityEdge, MessageEntity, DomainGraph

logger = logging.getLogger(__name__)


# ─── FTS5 Setup ───

FTS_CREATE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts_messages USING fts5(
    content,
    content='messages',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_conversations USING fts5(
    title,
    content='conversations',
    content_rowid='id',
    tokenize='porter unicode61'
);
"""

FTS_TRIGGERS_SQL = """
CREATE TRIGGER IF NOT EXISTS fts_messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO fts_messages(rowid, content) VALUES (new.id, COALESCE(new.content, ''));
END;

CREATE TRIGGER IF NOT EXISTS fts_messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO fts_messages(fts_messages, rowid, content) VALUES('delete', old.id, COALESCE(old.content, ''));
END;

CREATE TRIGGER IF NOT EXISTS fts_messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO fts_messages(fts_messages, rowid, content) VALUES('delete', old.id, COALESCE(old.content, ''));
    INSERT INTO fts_messages(rowid, content) VALUES (new.id, COALESCE(new.content, ''));
END;

CREATE TRIGGER IF NOT EXISTS fts_conversations_ai AFTER INSERT ON conversations BEGIN
    INSERT INTO fts_conversations(rowid, title) VALUES (new.id, COALESCE(new.title, ''));
END;

CREATE TRIGGER IF NOT EXISTS fts_conversations_ad AFTER DELETE ON conversations BEGIN
    INSERT INTO fts_conversations(fts_conversations, rowid, title) VALUES('delete', old.id, COALESCE(old.title, ''));
END;

CREATE TRIGGER IF NOT EXISTS fts_conversations_au AFTER UPDATE ON conversations BEGIN
    INSERT INTO fts_conversations(fts_conversations, rowid, title) VALUES('delete', old.id, COALESCE(old.title, ''));
    INSERT INTO fts_conversations(rowid, title) VALUES (new.id, COALESCE(new.title, ''));
END;
"""


async def init_fts():
    """Create FTS5 virtual tables and triggers."""
    async with engine.begin() as conn:
        for sql in FTS_CREATE_SQL.split(";"):
            sql = sql.strip()
            if sql:
                await conn.execute(text(sql))
        for sql in FTS_TRIGGERS_SQL.split(";"):
            sql = sql.strip()
            if sql:
                try:
                    await conn.execute(text(sql))
                except Exception:
                    pass  # Triggers may already exist

    # Ensure relationship_type column exists
    async with engine.begin() as conn:
        try:
            await conn.execute(text(
                "ALTER TABLE entity_edges ADD COLUMN relationship_type VARCHAR(100) DEFAULT 'co_occurrence'"
            ))
        except Exception:
            pass  # Column already exists

    logger.info("FTS5 tables initialized")


async def rebuild_fts():
    """Rebuild FTS index from scratch."""
    async with engine.begin() as conn:
        try:
            await conn.execute(text("INSERT INTO fts_messages(fts_messages) VALUES('rebuild')"))
        except Exception:
            pass
        try:
            await conn.execute(text("INSERT INTO fts_conversations(fts_conversations) VALUES('rebuild')"))
        except Exception:
            pass
    logger.info("FTS5 index rebuilt")


# ─── Search ───

async def search_local(query: str, limit: int = 20) -> dict:
    """Full-text search across all stored conversations and messages."""
    # Sanitize for FTS5 — strip special chars
    safe_query = re.sub(r'[^\w\s]', ' ', query).strip() or query
    async with async_session() as db:
        # Search messages
        msg_results = []
        try:
            rows = await db.execute(text("""
                SELECT m.id, m.conversation_id, m.role, m.content, m.sources,
                       c.title as conv_title,
                       bm25(fts_messages) as rank
                FROM fts_messages f
                JOIN messages m ON m.id = f.rowid
                JOIN conversations c ON c.id = m.conversation_id
                WHERE fts_messages MATCH :q
                ORDER BY rank
                LIMIT :limit
            """), {"q": safe_query, "limit": limit})
            for row in rows:
                msg_results.append({
                    "type": "message",
                    "id": row[0],
                    "conversation_id": row[1],
                    "role": row[2],
                    "content": row[3][:500] if row[3] else "",
                    "sources": row[4] or [],
                    "conversation_title": row[5],
                    "rank": row[6],
                })
        except Exception as e:
            logger.warning(f"FTS message search failed: {e}")

        # Search conversations
        conv_results = []
        try:
            rows = await db.execute(text("""
                SELECT c.id, c.title, c.mode, c.updated_at,
                       bm25(fts_conversations) as rank
                FROM fts_conversations f
                JOIN conversations c ON c.id = f.rowid
                WHERE fts_conversations MATCH :q
                ORDER BY rank
                LIMIT :limit
            """), {"q": safe_query, "limit": limit})
            for row in rows:
                conv_results.append({
                    "type": "conversation",
                    "id": row[0],
                    "title": row[1],
                    "mode": row[2],
                    "updated_at": str(row[3]),
                    "rank": row[4],
                })
        except Exception as e:
            logger.warning(f"FTS conversation search failed: {e}")

        # Search entities
        entity_results = []
        rows = await db.execute(
            select(Entity).where(Entity.name.contains(query)).limit(10)
        )
        for entity in rows.scalars().all():
            entity_results.append({
                "type": "entity",
                "id": entity.id,
                "name": entity.name,
                "entity_type": entity.entity_type,
                "mention_count": entity.mention_count,
            })

        return {
            "query": query,
            "messages": msg_results,
            "conversations": conv_results,
            "entities": entity_results,
            "total": len(msg_results) + len(conv_results) + len(entity_results),
        }


# ─── Entity Extraction ───

# Stop words to filter from entity candidates
STOP_ENTITIES = {
    "New Chat", "The", "This", "That", "These", "Those", "What", "How",
    "Why", "When", "Where", "Which", "Can", "Could", "Should", "Based",
    "Thank", "Thanks", "Hello", "Please", "There", "Here", "Just",
    "Also", "Like", "Would", "About", "Much", "Some", "Well", "Sure",
    "Good", "Great", "Right", "First", "Second", "Third", "Best",
}


def extract_candidates_regex(content: str) -> list[str]:
    """Extract entity candidates using regex patterns (fast, no LLM)."""
    entities = set()

    # Quoted terms
    entities.update(re.findall(r'"([^"]{2,50})"', content))

    # Capitalized multi-word phrases (proper nouns, tech names)
    entities.update(re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', content))

    # Technical terms (word.word or CamelCase)
    entities.update(re.findall(r'\b([A-Za-z]+(?:\.[A-Za-z]+)+)\b', content))

    # Filter
    return [e for e in entities if len(e) > 3 and e not in STOP_ENTITIES]


async def enrich_entities_with_llm(content: str, candidates: list[str]) -> list[dict]:
    """Use LLM to type entities and identify relationships.

    Returns list of dicts: {"name": str, "type": str, "relationships": [{"target": str, "type": str}]}
    """
    from core.llm import call_llm

    prompt = f"""Analyze these entity candidates extracted from a message and return structured JSON.

Candidates: {json.dumps(candidates[:15])}

Message excerpt (first 1500 chars):
{content[:1500]}

For each entity, determine its type and relationships to other entities in the list.
Return ONLY a JSON array:
[
  {{
    "name": "Entity Name",
    "type": "person|org|tech|concept|place|event|topic",
    "relationships": [{{"target": "Other Entity", "type": "related_to|part_of|created_by|uses|competes_with|located_in"}}]
  }}
]

Only include entities you can confidently classify. Skip generic or common words."""

    messages = [
        {"role": "system", "content": "You are an entity extraction assistant. Return only valid JSON arrays."},
        {"role": "user", "content": prompt},
    ]

    try:
        result = await call_llm(messages)
        cleaned = result.strip()
        # Strip markdown code fences
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)
        entities = json.loads(cleaned)
        if isinstance(entities, list):
            return entities
    except (json.JSONDecodeError, Exception) as e:
        logger.debug(f"LLM entity enrichment failed: {e}")

    return []


async def extract_and_store_entities(message_id: int, content: str):
    """Extract entities/topics from a message and store them in the graph.

    Strategy: regex candidates → LLM enrichment (if enough candidates) → typed upsert → edges
    """
    if not content or len(content) < 50:
        return

    # Step 1: Regex candidates
    candidates = extract_candidates_regex(content)
    if not candidates:
        return

    # Step 2: LLM enrichment (only when content is substantial and we have enough candidates)
    enriched = []
    if len(content) > 100 and len(candidates) >= 2:
        try:
            enriched = await enrich_entities_with_llm(content, candidates)
        except Exception:
            pass  # Non-critical

    # Step 3: Build entity list — prefer enriched data, fall back to regex
    entity_data = []  # list of {"name": str, "type": str, "relationships": [...]}

    if enriched:
        # Use LLM-enriched data
        for e in enriched:
            name = e.get("name", "").strip()
            if name and len(name) > 1:
                entity_data.append({
                    "name": name,
                    "type": e.get("type", "topic"),
                    "relationships": e.get("relationships", []),
                })
    else:
        # Regex-only fallback
        for name in candidates[:10]:
            entity_data.append({"name": name, "type": "topic", "relationships": []})

    if not entity_data:
        return

    async with async_session() as db:
        entity_objects = []
        for ed in entity_data[:10]:  # Cap at 10 entities per message
            # Upsert entity
            result = await db.execute(
                select(Entity).where(Entity.name == ed["name"])
            )
            entity = result.scalar_one_or_none()
            if entity:
                entity.mention_count += 1
                entity.last_seen = datetime.now(timezone.utc)
                # Upgrade type if we have a better one
                if ed["type"] != "topic" and entity.entity_type == "topic":
                    entity.entity_type = ed["type"]
            else:
                entity = Entity(name=ed["name"], entity_type=ed["type"])
                db.add(entity)
                await db.flush()
            entity_objects.append((entity, ed))

            # Link message to entity
            existing = await db.execute(
                select(MessageEntity).where(
                    MessageEntity.message_id == message_id,
                    MessageEntity.entity_id == entity.id,
                )
            )
            if not existing.scalar_one_or_none():
                db.add(MessageEntity(message_id=message_id, entity_id=entity.id))

        # Build entity-entity edges
        # First: co-occurrence edges (all pairs)
        entity_only = [eo[0] for eo in entity_objects]
        for i, e1 in enumerate(entity_only):
            for e2 in entity_only[i+1:]:
                result = await db.execute(
                    select(EntityEdge).where(
                        EntityEdge.source_id == e1.id,
                        EntityEdge.target_id == e2.id,
                    )
                )
                edge = result.scalar_one_or_none()
                if edge:
                    edge.weight += 1.0
                    edge.updated_at = datetime.now(timezone.utc)
                else:
                    db.add(EntityEdge(source_id=e1.id, target_id=e2.id, weight=1.0))

        # Then: typed relationship edges from LLM enrichment
        for entity, ed in entity_objects:
            for rel in ed.get("relationships", []):
                target_name = rel.get("target", "")
                rel_type = rel.get("type", "related_to")
                if not target_name:
                    continue
                # Find the target entity
                target_result = await db.execute(
                    select(Entity).where(Entity.name == target_name)
                )
                target = target_result.scalar_one_or_none()
                if target and target.id != entity.id:
                    # Check if edge exists
                    edge_result = await db.execute(
                        select(EntityEdge).where(
                            EntityEdge.source_id == entity.id,
                            EntityEdge.target_id == target.id,
                        )
                    )
                    edge = edge_result.scalar_one_or_none()
                    if edge:
                        # Upgrade relationship type if it was generic
                        if edge.relationship_type == "co_occurrence":
                            edge.relationship_type = rel_type
                    else:
                        db.add(EntityEdge(
                            source_id=entity.id,
                            target_id=target.id,
                            weight=1.0,
                            relationship_type=rel_type,
                        ))

        await db.commit()


# ─── Domain Graph ───

async def update_domain_graph(sources: list[dict]):
    """Update domain co-occurrence graph from sources in a message."""
    if not sources:
        return

    from urllib.parse import urlparse
    domains = list(set(
        urlparse(s.get("url", "")).netloc.replace("www.", "")
        for s in sources if s.get("url")
    ))

    if len(domains) < 2:
        return

    async with async_session() as db:
        for i, d1 in enumerate(domains):
            for d2 in domains[i+1:]:
                result = await db.execute(
                    select(DomainGraph).where(
                        DomainGraph.domain_a == d1,
                        DomainGraph.domain_b == d2,
                    )
                )
                edge = result.scalar_one_or_none()
                if edge:
                    edge.co_occurrence += 1
                else:
                    db.add(DomainGraph(domain_a=d1, domain_b=d2))
        await db.commit()


# ─── Graph Query ───

async def get_entity_graph(entity_name: str, depth: int = 2) -> dict:
    """Get the knowledge graph around an entity."""
    async with async_session() as db:
        # Find entity
        result = await db.execute(select(Entity).where(Entity.name == entity_name))
        entity = result.scalar_one_or_none()
        if not entity:
            return {"entity": None, "related": [], "conversations": []}

        # Get connected entities
        edges = await db.execute(
            select(EntityEdge, Entity)
            .join(Entity, Entity.id == EntityEdge.target_id)
            .where(EntityEdge.source_id == entity.id)
            .order_by(EntityEdge.weight.desc())
            .limit(20)
        )
        related = []
        for edge, connected in edges.all():
            related.append({
                "name": connected.name,
                "type": connected.entity_type,
                "weight": edge.weight,
                "relationship": edge.relationship_type,
            })

        # Also get reverse edges
        rev_edges = await db.execute(
            select(EntityEdge, Entity)
            .join(Entity, Entity.id == EntityEdge.source_id)
            .where(EntityEdge.target_id == entity.id)
            .order_by(EntityEdge.weight.desc())
            .limit(20)
        )
        for edge, connected in rev_edges.all():
            if not any(r["name"] == connected.name for r in related):
                related.append({
                    "name": connected.name,
                    "type": connected.entity_type,
                    "weight": edge.weight,
                    "relationship": edge.relationship_type,
                })

        # Get conversations mentioning this entity
        msg_entities = await db.execute(
            select(Message.conversation_id, Conversation.title)
            .join(Message, Message.id == MessageEntity.message_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(MessageEntity.entity_id == entity.id)
            .distinct()
            .limit(20)
        )
        conversations = [
            {"id": row[0], "title": row[1]}
            for row in msg_entities.all()
        ]

        return {
            "entity": {
                "id": entity.id,
                "name": entity.name,
                "type": entity.entity_type,
                "mentions": entity.mention_count,
                "description": entity.description,
            },
            "related": related,
            "conversations": conversations,
        }


# ─── Tags ───

async def tag_conversation(conversation_id: int, tag_name: str) -> dict:
    async with async_session() as db:
        # Upsert tag
        result = await db.execute(select(Tag).where(Tag.name == tag_name))
        tag = result.scalar_one_or_none()
        if not tag:
            tag = Tag(name=tag_name)
            db.add(tag)
            await db.flush()

        # Link
        existing = await db.execute(
            select(ConversationTag).where(
                ConversationTag.conversation_id == conversation_id,
                ConversationTag.tag_id == tag.id,
            )
        )
        if not existing.scalar_one_or_none():
            db.add(ConversationTag(conversation_id=conversation_id, tag_id=tag.id))
            await db.commit()

        return {"tag": tag_name, "conversation_id": conversation_id}


async def untag_conversation(conversation_id: int, tag_name: str):
    async with async_session() as db:
        result = await db.execute(select(Tag).where(Tag.name == tag_name))
        tag = result.scalar_one_or_none()
        if tag:
            await db.execute(
                delete(ConversationTag).where(
                    ConversationTag.conversation_id == conversation_id,
                    ConversationTag.tag_id == tag.id,
                )
            )
            await db.commit()


async def list_tags():
    async with async_session() as db:
        rows = await db.execute(select(Tag).order_by(Tag.name))
        tags = rows.scalars().all()
        out = []
        for t in tags:
            count = await db.execute(
                select(func.count()).select_from(ConversationTag).where(ConversationTag.tag_id == t.id)
            )
            out.append({"id": t.id, "name": t.name, "color": t.color, "count": count.scalar()})
        return out


async def get_conversation_tags(conversation_id: int):
    async with async_session() as db:
        rows = await db.execute(
            select(Tag).join(ConversationTag, ConversationTag.tag_id == Tag.id)
            .where(ConversationTag.conversation_id == conversation_id)
        )
        return [{"id": t.id, "name": t.name, "color": t.color} for t in rows.scalars().all()]


async def get_all_stats() -> dict:
    """Get knowledge graph statistics."""
    async with async_session() as db:
        conv_count = (await db.execute(select(func.count()).select_from(Conversation))).scalar()
        msg_count = (await db.execute(select(func.count()).select_from(Message))).scalar()
        entity_count = (await db.execute(select(func.count()).select_from(Entity))).scalar()
        edge_count = (await db.execute(select(func.count()).select_from(EntityEdge))).scalar()
        tag_count = (await db.execute(select(func.count()).select_from(Tag))).scalar()
        domain_count = (await db.execute(select(func.count()).select_from(DomainGraph))).scalar()

        # Top entities
        top_entities = await db.execute(
            select(Entity).order_by(Entity.mention_count.desc()).limit(10)
        )

        return {
            "conversations": conv_count,
            "messages": msg_count,
            "entities": entity_count,
            "edges": edge_count,
            "tags": tag_count,
            "domains_linked": domain_count,
            "top_entities": [
                {"name": e.name, "type": e.entity_type, "mentions": e.mention_count}
                for e in top_entities.scalars().all()
            ],
        }


# ─── Context helpers for dynamic prompts ───

async def get_recent_entities(hours: int = 24, limit: int = 5) -> list[dict]:
    """Get the most recently mentioned entities across all conversations."""
    async with async_session() as db:
        cutoff = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        from sqlalchemy import func as sa_func
        cutoff = datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=hours)
        result = await db.execute(
            select(Entity)
            .where(Entity.last_seen >= cutoff)
            .order_by(Entity.mention_count.desc())
            .limit(limit)
        )
        return [
            {"name": e.name, "type": e.entity_type, "mentions": e.mention_count}
            for e in result.scalars().all()
        ]


async def get_conversation_entities(conversation_id: int, limit: int = 8) -> list[dict]:
    """Get entities specific to a conversation."""
    async with async_session() as db:
        result = await db.execute(
            select(Entity, MessageEntity.relevance)
            .join(MessageEntity, MessageEntity.entity_id == Entity.id)
            .join(Message, Message.id == MessageEntity.message_id)
            .where(Message.conversation_id == conversation_id)
            .distinct()
            .limit(limit)
        )
        return [
            {"name": e.name, "type": e.entity_type}
            for e, _ in result.all()
        ]
