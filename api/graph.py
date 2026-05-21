"""Graph + Search + Tag API — Local knowledge graph endpoints."""

from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from core.graph import (
    search_local, get_entity_graph, get_all_stats,
    tag_conversation, untag_conversation, list_tags, get_conversation_tags,
    extract_and_store_entities, update_domain_graph,
    init_fts, rebuild_fts,
)

router = APIRouter(tags=["graph"])


class TagRequest(BaseModel):
    tag: str
    conversation_id: int


class UntagRequest(BaseModel):
    tag: str
    conversation_id: int


@router.get("/graph/stats")
async def stats():
    """Knowledge graph statistics."""
    return await get_all_stats()


@router.get("/graph/search")
async def local_search(q: str = Query(...), limit: int = Query(20)):
    """Full-text search across all stored conversations, messages, and entities."""
    return await search_local(q, limit)


@router.get("/graph/entity/{entity_name:path}")
async def entity_graph(entity_name: str, depth: int = Query(2)):
    """Get the knowledge graph around an entity."""
    return await get_entity_graph(entity_name, depth)


# ─── Tags ───

@router.get("/tags")
async def get_tags():
    return await list_tags()


@router.get("/tags/conversation/{conv_id}")
async def get_conv_tags(conv_id: int):
    return await get_conversation_tags(conv_id)


@router.post("/tags/add")
async def add_tag(req: TagRequest):
    return await tag_conversation(req.conversation_id, req.tag)


@router.post("/tags/remove")
async def remove_tag(req: UntagRequest):
    await untag_conversation(req.conversation_id, req.tag)
    return {"ok": True}


# ─── FTS Maintenance ───

@router.post("/graph/rebuild")
async def rebuild():
    """Rebuild FTS5 index from scratch."""
    await rebuild_fts()
    return {"status": "rebuilt"}


@router.post("/graph/init")
async def init():
    """Initialize FTS5 tables."""
    await init_fts()
    return {"status": "initialized"}
