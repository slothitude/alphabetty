"""Extension endpoints — health, handshake, WebSocket command relay."""

import asyncio
import json
import secrets
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import get_current_user, get_db, _validate_api_key, _validate_jwt
from models.user import User, ApiKey

router = APIRouter(prefix="/ext", tags=["extension"])

VERSION = "0.1.0"

# ── In-memory state ──
_ext_connections: dict[int, WebSocket] = {}  # user_id -> WebSocket
_pending_commands: dict[str, dict] = {}       # command_id -> {event, result}


# ── Unauthenticated ──

@router.get("/ping")
async def ping():
    """Unauthenticated check — is this an Alphabetty server?"""
    return {"server": "alphabetty", "version": VERSION}


# ── Auto-connect (cookie-based) ──

@router.get("/handshake")
async def handshake(request: Request, db: AsyncSession = Depends(get_db)):
    """Auto-connect: validate JWT cookie, create/replace extension API key.

    Called from the page's own JS (same-origin), so HttpOnly cookie is sent.
    """
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(401, "Not authenticated")

    user = await _validate_jwt(token, db)
    if not user or not user.is_active:
        raise HTTPException(401, "Invalid token")

    # Delete existing extension key (one per user, refreshed on each handshake)
    result = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.name == "extension")
    )
    existing = result.scalar_one_or_none()
    if existing:
        await db.delete(existing)
        await db.commit()

    # Create fresh API key
    from core.auth import create_api_key
    raw_key, _ = await create_api_key(user.id, "extension", db)

    return {
        "version": VERSION,
        "user": user.username,
        "apiKey": raw_key,
    }


# ── Authenticated health ──

@router.get("/health")
async def extension_health(user: User = Depends(get_current_user)):
    """Validate extension connection. Auth via API key (Bearer header)."""
    return {
        "version": VERSION,
        "user": user.username,
        "status": "ok",
    }


# ── WebSocket command relay ──

@router.websocket("/ws")
async def ext_ws(websocket: WebSocket):
    await websocket.accept()

    # Auth: first message must be {type: "auth", token: "..."}
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        auth_msg = json.loads(raw)
    except Exception:
        await websocket.close(4001, "Auth required")
        return

    if auth_msg.get("type") != "auth" or not auth_msg.get("token"):
        await websocket.close(4001, "Auth required")
        return

    token = auth_msg["token"]
    from app import async_session
    async with async_session() as db:
        if token.startswith("alph_"):
            user = await _validate_api_key(token, db)
        else:
            user = await _validate_jwt(token, db)

    if not user or not user.is_active:
        await websocket.close(4001, "Invalid token")
        return

    # Register connection
    _ext_connections[user.id] = websocket
    await websocket.send_text(json.dumps({"type": "auth_ok", "user": user.username}))

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "result":
                cmd_id = msg.get("id")
                if cmd_id in _pending_commands:
                    _pending_commands[cmd_id]["result"] = msg.get("result")
                    _pending_commands[cmd_id]["event"].set()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _ext_connections.pop(user.id, None)


# ── Execute command (for agents / MCP / n8n) ──

class ExtCommandRequest(BaseModel):
    action: str       # evaluate, click, type, navigate, getDOM, getText
    params: dict = {}


@router.post("/execute")
async def execute_command(cmd: ExtCommandRequest, user: User = Depends(get_current_user)):
    """Send a command to the connected extension and wait for the result."""
    ws = _ext_connections.get(user.id)
    if not ws:
        raise HTTPException(503, "No extension connected")

    cmd_id = secrets.token_urlsafe(8)
    event = asyncio.Event()
    _pending_commands[cmd_id] = {"event": event, "result": None}

    try:
        await ws.send_text(json.dumps({
            "type": "command",
            "id": cmd_id,
            "action": cmd.action,
            "params": cmd.params,
        }))

        await asyncio.wait_for(event.wait(), timeout=30)
        result = _pending_commands.pop(cmd_id)
        return result["result"]
    except asyncio.TimeoutError:
        _pending_commands.pop(cmd_id, None)
        raise HTTPException(504, "Extension command timed out")
    except Exception as e:
        _pending_commands.pop(cmd_id, None)
        raise HTTPException(500, str(e))
