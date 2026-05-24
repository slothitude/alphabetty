"""Media API — minimal router. Real media tools live in mcp_server.py (MCP-only)."""

from fastapi import APIRouter

router = APIRouter(tags=["media"])


@router.get("/media/health")
async def media_health():
    return {"status": "ok", "note": "Media tools are available via MCP (media_search, media_control, etc.)"}
