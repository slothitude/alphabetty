"""Graph + Search + Tag API — Local knowledge graph endpoints."""

from typing import Optional

from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel

from core.auth import get_optional_user
from core.graph import (
    search_local, get_entity_graph, get_all_stats,
    tag_conversation, untag_conversation, list_tags, get_conversation_tags,
    extract_and_store_entities, update_domain_graph,
    init_fts, rebuild_fts,
)
from models.user import User

router = APIRouter(tags=["graph"])


class TagRequest(BaseModel):
    tag: str
    conversation_id: int


class UntagRequest(BaseModel):
    tag: str
    conversation_id: int


@router.get("/graph/stats")
async def stats(user: User | None = Depends(get_optional_user)):
    """Knowledge graph statistics."""
    return await get_all_stats()


@router.get("/graph/search")
async def local_search(q: str = Query(...), limit: int = Query(20), user: User | None = Depends(get_optional_user)):
    """Full-text search across all stored conversations, messages, and entities."""
    return await search_local(q, limit)


@router.get("/graph/entity/{entity_name:path}")
async def entity_graph(entity_name: str, depth: int = Query(2), user: User | None = Depends(get_optional_user)):
    """Get the knowledge graph around an entity."""
    return await get_entity_graph(entity_name, depth)


# ─── Tags ───

@router.get("/tags")
async def get_tags(user: User | None = Depends(get_optional_user)):
    return await list_tags()


@router.get("/tags/conversation/{conv_id}")
async def get_conv_tags(conv_id: int, user: User | None = Depends(get_optional_user)):
    return await get_conversation_tags(conv_id)


@router.post("/tags/add")
async def add_tag(req: TagRequest, user: User | None = Depends(get_optional_user)):
    return await tag_conversation(req.conversation_id, req.tag)


@router.post("/tags/remove")
async def remove_tag(req: UntagRequest, user: User | None = Depends(get_optional_user)):
    await untag_conversation(req.conversation_id, req.tag)
    return {"ok": True}


# ─── FTS Maintenance ───

@router.post("/graph/rebuild")
async def rebuild(user: User | None = Depends(get_optional_user)):
    """Rebuild FTS5 index from scratch."""
    await rebuild_fts()
    return {"status": "rebuilt"}


@router.post("/graph/init")
async def init(user: User | None = Depends(get_optional_user)):
    """Initialize FTS5 tables."""
    await init_fts()
    return {"status": "initialized"}
