"""Outbound agent bridge — Alphabetty calls other agents via MCP/HTTP APIs.

Supports:
- Generic MCP tool calls to configured agent endpoints
- Self-call via /v1/tools/call
- Delegation of sub-tasks to other agents
"""

import json
import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Configurable agent endpoints — set via ALPHABETTY_AGENT_ENDPOINTS env var
# Format: {"name": "http://host:port", ...}
_agent_endpoints: dict[str, str] = {}


def _get_endpoints() -> dict[str, str]:
    """Lazy-load agent endpoints from settings."""
    global _agent_endpoints
    if not _agent_endpoints and hasattr(settings, "agent_endpoints") and settings.agent_endpoints:
        for entry in settings.agent_endpoints.split(","):
            if "=" in entry:
                name, url = entry.split("=", 1)
                _agent_endpoints[name.strip()] = url.strip()
    return _agent_endpoints


async def call_tool(server: str, tool: str, args: dict = None) -> dict:
    """Call a tool on a remote MCP/HTTP agent server.

    For MCP-over-HTTP servers that expose /v1/tools/call.
    """
    endpoints = _get_endpoints()
    base_url = endpoints.get(server)
    if not base_url:
        # Check if it's a direct URL
        if server.startswith("http"):
            base_url = server.rstrip("/")
        else:
            return {"error": f"Unknown agent server: {server}. Available: {list(endpoints.keys())}"}

    url = f"{base_url}/v1/tools/call"
    payload = {
        "name": tool,
        "arguments": args or {},
    }
    headers = {"Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return {"status": "ok", "server": server, "tool": tool, "result": data}
    except Exception as e:
        logger.error(f"Agent bridge call failed ({server}/{tool}): {e}")
        return {"error": str(e), "server": server, "tool": tool}


async def call_alphabetty(tool: str, args: dict = None) -> dict:
    """Self-call via the local /v1/tools/call endpoint."""
    url = f"http://localhost:{settings.port}/v1/tools/call"
    payload = {
        "name": tool,
        "arguments": args or {},
    }
    headers = {"Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Self-call failed ({tool}): {e}")
        return {"error": str(e)}


async def delegate(task: str, agent: Optional[str] = None) -> dict:
    """Delegate a sub-task to another agent.

    If agent is None, uses the first available configured agent.
    Sends the task as a natural language instruction.
    """
    endpoints = _get_endpoints()
    if not endpoints:
        return {"error": "No agent endpoints configured. Set ALPHABETTY_AGENT_ENDPOINTS."}

    target = agent or list(endpoints.keys())[0]
    base_url = endpoints.get(target)
    if not base_url:
        return {"error": f"Agent '{target}' not found. Available: {list(endpoints.keys())}"}

    # Try /v1/chat/completions for natural language interaction
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "messages": [
            {"role": "system", "content": "You are a delegated sub-agent. Complete the task concisely and return results."},
            {"role": "user", "content": task},
        ],
        "max_tokens": 8192,
    }
    headers = {"Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"status": "delegated", "agent": target, "result": content}
    except Exception as e:
        # Fallback: try MCP tool call format
        logger.warning(f"Chat delegation to {target} failed ({e}), trying tool call")
        return await call_tool(target, "complete_task", {"task": task})


def list_agents() -> dict:
    """List available agent endpoints."""
    endpoints = _get_endpoints()
    return {"agents": [{"name": k, "url": v} for k, v in endpoints.items()]}


async def call_tool_authenticated(base_url: str, tool: str, args: dict = None) -> dict:
    """Call a tool on a remote swarm peer with X-Swarm-Key authentication."""
    url = f"{base_url.rstrip('/')}/api/v1/swarm/execute"
    payload = {"tool": tool, "args": args or {}}
    headers = {
        "Content-Type": "application/json",
        "X-Swarm-Key": settings.swarm_key,
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Authenticated swarm call failed ({base_url}/{tool}): {e}")
        return {"error": str(e)}
