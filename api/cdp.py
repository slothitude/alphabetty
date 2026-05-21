import json
import logging
import asyncio
import random
from typing import Optional

import httpx
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel

from config import settings
from core.cdp_bridge import cdp

router = APIRouter(tags=["cdp"])
logger = logging.getLogger(__name__)


class NavigateRequest(BaseModel):
    url: str
    tab_id: Optional[str] = None


class QueryRequest(BaseModel):
    selector: str
    tab_id: Optional[str] = None


class EvaluateRequest(BaseModel):
    expression: str
    tab_id: Optional[str] = None


class ClickRequest(BaseModel):
    selector: str
    tab_id: Optional[str] = None


class TypeRequest(BaseModel):
    selector: str
    text: str
    tab_id: Optional[str] = None


class ExtractRequest(BaseModel):
    expression: str
    tab_id: Optional[str] = None


class ScrollRequest(BaseModel):
    x: int = 0
    y: int = 300
    tab_id: Optional[str] = None


class CookieRequest(BaseModel):
    name: str
    value: str
    domain: str
    path: str = "/"


# ─── Tab management ───

@router.get("/cdp/tabs")
async def list_tabs():
    return {"tabs": await cdp.get_tabs()}


@router.get("/cdp/status")
async def chrome_status():
    """Check if Chrome is running and undetected."""
    try:
        tabs = await cdp.get_tabs()
        checks = await cdp.is_undetected()
        ua = await cdp.user_agent()
        return {
            "status": "running",
            "tab_count": len(tabs),
            "user_agent": ua,
            "stealth": checks,
        }
    except Exception as e:
        return {"status": "offline", "error": str(e)}


# ─── Navigation ───

@router.post("/cdp/navigate")
async def navigate(req: NavigateRequest):
    result = await cdp.navigate(req.url)
    return {"status": "ok", "result": result}


@router.get("/cdp/content")
async def get_content(tab_id: Optional[str] = None):
    text = await cdp.get_content()
    return {"content": text}


@router.get("/cdp/dom")
async def get_dom(tab_id: Optional[str] = None, depth: int = Query(3)):
    dom = await cdp.get_dom(depth)
    return {"dom": dom}


# ─── DOM queries ───

@router.post("/cdp/query")
async def query_selector(req: QueryRequest):
    node_id = await cdp.query_selector(req.selector)
    return {"nodeId": node_id}


@router.post("/cdp/evaluate")
async def evaluate(req: EvaluateRequest):
    result = await cdp.evaluate(req.expression)
    return {"result": result}


# ─── Human-like interactions ───

@router.post("/cdp/click")
async def click_element(req: ClickRequest):
    """Human-like click with random offset and timing."""
    result = await cdp.click(req.selector)
    return result


@router.post("/cdp/type")
async def type_text(req: TypeRequest):
    """Human-like typing with random delays between keystrokes."""
    result = await cdp.type_text(req.selector, req.text)
    return result


@router.post("/cdp/scroll")
async def scroll_page(req: ScrollRequest):
    """Human-like scroll."""
    result = await cdp.scroll(req.x, req.y)
    return result


@router.get("/cdp/screenshot")
async def screenshot(tab_id: Optional[str] = None, format: str = Query("png")):
    data = await cdp.screenshot(format)
    from fastapi.responses import Response
    return Response(content=data, media_type=f"image/{format}")


@router.post("/cdp/extract")
async def extract_data(req: ExtractRequest):
    data = await cdp.evaluate(req.expression)
    return {"data": data}


# ─── Cookies ───

@router.get("/cdp/cookies")
async def get_cookies():
    cookies = await cdp.get_cookies()
    return {"cookies": cookies}


@router.post("/cdp/cookies")
async def set_cookie(req: CookieRequest):
    result = await cdp.set_cookie(req.name, req.value, req.domain, req.path)
    return {"status": "ok", "result": result}


# ─── WebSocket bridge ───

@router.websocket("/ws/cdp")
async def cdp_websocket(ws: WebSocket):
    """WebSocket bridge for real-time CDP events (highlight on hover, etc.)."""
    await ws.accept()
    try:
        ws_url = await cdp.get_ws_url()
        async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as cdp_ws:
            while True:
                data = await ws.receive_text()
                msg = json.loads(data)
                await cdp_ws.send(json.dumps(msg))
                resp = await cdp_ws.recv()
                await ws.send_text(resp)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"CDP WebSocket error: {e}")
