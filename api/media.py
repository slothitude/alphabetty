"""Media API — health check + tool proxy."""

import json

from fastapi import APIRouter

from core.media import get_tools, call_tool

router = APIRouter(tags=["media"])


@router.get("/media/health")
async def media_health():
    return {"status": "ok"}


@router.get("/media/tools")
async def media_tools():
    """Return all media stack tool schemas."""
    return await get_tools()


@router.post("/media/call")
async def media_call(body: dict):
    """Proxy a media tool call. Body: {"name": "...", "arguments": {...}}"""
    name = body.get("name", "")
    arguments = body.get("arguments", {})
    result = await call_tool(name, arguments)
    return result
