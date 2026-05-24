"""Media stack proxy — calls the OpenAI-compatible media tool API on Rog."""

import json
import logging

import httpx

logger = logging.getLogger(__name__)

MEDIA_STACK_URL = "http://192.168.0.33:8070"

# Cached tool schemas (fetched once)
_media_tools: list[dict] | None = None


async def get_tools() -> list[dict]:
    """Fetch OpenAI-format tool definitions from the media stack API."""
    global _media_tools
    if _media_tools is not None:
        return _media_tools
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{MEDIA_STACK_URL}/v1/tools")
            r.raise_for_status()
            _media_tools = r.json()
            logger.info(f"Loaded {len(_media_tools)} media stack tools")
            return _media_tools
    except Exception as e:
        logger.warning(f"Failed to fetch media tools: {e}")
        return []


def get_tools_sync() -> list[dict]:
    """Synchronous version for module-level init."""
    global _media_tools
    if _media_tools is not None:
        return _media_tools
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{MEDIA_STACK_URL}/v1/tools")
            r.raise_for_status()
            _media_tools = r.json()
            return _media_tools
    except Exception as e:
        logger.warning(f"Failed to fetch media tools: {e}")
        return []


async def call_tool(name: str, arguments: dict) -> dict:
    """Proxy a tool call to the media stack API."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{MEDIA_STACK_URL}/v1/tool-call",
                json={"function": {"name": name, "arguments": json.dumps(arguments)}},
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"error": str(e)}


# Known tool names — used by agent.py to route without waiting for API
TOOL_NAMES = {
    "media_search", "search_tmdb", "media_request", "media_requests",
    "radarr_movies", "radarr_queue", "sonarr_series", "sonarr_queue",
    "torrents_list", "torrents_action", "stack_status", "vpn_status",
    "media_control",
}
