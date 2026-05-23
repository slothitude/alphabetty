"""Swarm REST API — inter-instance communication endpoints.

Unauthenticated status endpoint for health checks.
Authenticated execute/llm endpoints for remote tool execution (X-Swarm-Key).
"""

import logging

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from config import settings
from core.swarm import swarm

router = APIRouter(prefix="/swarm", tags=["swarm"])
logger = logging.getLogger(__name__)


def _verify_swarm_key(key: str | None):
    """Verify the swarm shared key."""
    if not settings.swarm_key:
        raise HTTPException(status_code=403, detail="Swarm not configured")
    if key != settings.swarm_key:
        raise HTTPException(status_code=403, detail="Invalid swarm key")


class ExecuteRequest(BaseModel):
    tool: str
    args: dict = {}


class LLMRequest(BaseModel):
    messages: list[dict]
    model: str | None = None
    intent: str = "agent"
    max_tokens: int = 16384
    stream: bool = False


@router.get("/status")
async def get_status():
    """Instance status + peer health. Used by peers for health checks."""
    return swarm.status()


@router.post("/execute")
async def execute_remote(req: ExecuteRequest, x_swarm_key: str = Header(default=None, alias="X-Swarm-Key")):
    """Execute a tool on this instance on behalf of a remote peer."""
    _verify_swarm_key(x_swarm_key)

    from api.agent import execute_tool
    result = await execute_tool(req.tool, req.args)
    return result


@router.post("/llm")
async def llm_remote(req: LLMRequest, x_swarm_key: str = Header(default=None, alias="X-Swarm-Key")):
    """Proxy an LLM call through this instance's provider pool."""
    _verify_swarm_key(x_swarm_key)

    from core.providers import router as provider_router
    client, resp = await provider_router.call(
        messages=req.messages,
        model=req.model,
        intent=req.intent,
        max_tokens=req.max_tokens,
        stream=False,
    )
    try:
        data = resp.json()
        return data
    finally:
        await client.aclose()


@router.post("/check")
async def force_check(x_swarm_key: str = Header(default=None, alias="X-Swarm-Key")):
    """Force a peer health refresh."""
    _verify_swarm_key(x_swarm_key)
    await swarm.health_check_all()
    return swarm.status()
