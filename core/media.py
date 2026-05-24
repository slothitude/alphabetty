"""Minimal Jellyfin stubs — real media tools are in mcp_server.py (MCP-only).

These stubs exist so api/agent.py can import core.media without crashing.
The MCP tools (media_search, media_request, etc.) are the primary interface.
"""

import json

JELLYFIN_LAN = "http://192.168.0.33:8096"


async def jellyfin_search(query: str) -> list:
    """Search Jellyfin. Returns empty list — use MCP media_search tool instead."""
    return []


def jellyfin_stream_url(item_id: str) -> str:
    """Build a direct-play stream URL for a Jellyfin item."""
    return f"{JELLYFIN_LAN}/Videos/{item_id}/stream.mp4?mediaSourceId={item_id}"


def jellyfin_poster_url(item_id: str) -> str:
    """Build a poster image URL for a Jellyfin item."""
    return f"{JELLYFIN_LAN}/Items/{item_id}/Images/Primary"
