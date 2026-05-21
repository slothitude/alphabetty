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
from core.macro import recorder
from core.recording import screen_recorder

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


class MacroNameRequest(BaseModel):
    name: str
    url: str = ""


class MacroPlayRequest(BaseModel):
    macro_id: int


class ScreenRecordRequest(BaseModel):
    fps: int = 10
    quality: int = 80


class YouTubeRequest(BaseModel):
    query: str


class TabUrlRequest(BaseModel):
    url: str = "about:blank"


class TabIdRequest(BaseModel):
    target_id: str


# ─── Tab management ───

@router.get("/cdp/tabs")
async def list_tabs():
    return {"tabs": await cdp.get_tabs()}


@router.post("/cdp/tab/new")
async def create_tab(req: TabUrlRequest):
    """Open a new Chrome tab and navigate to URL."""
    result = await cdp.create_tab(req.url)
    return result


@router.post("/cdp/tab/close")
async def close_tab(req: TabIdRequest):
    """Close a Chrome tab by target ID."""
    result = await cdp.close_tab(req.target_id)
    return result


@router.post("/cdp/tab/activate")
async def activate_tab(req: TabIdRequest):
    """Activate (focus) a Chrome tab by target ID."""
    result = await cdp.activate_tab(req.target_id)
    return result


@router.get("/cdp/print-pdf")
async def print_pdf():
    """Print the current page as PDF."""
    result = await cdp.print_pdf()
    return result


@router.post("/cdp/wait-for")
async def wait_for_element(req: QueryRequest):
    """Wait for an element matching a CSS selector to appear."""
    result = await cdp.wait_for_selector(req.selector, timeout=10000)
    return result


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


# ─── Macro Recording (API-level) ───

@router.post("/cdp/macro/record/start")
async def macro_record_start(req: MacroNameRequest):
    """Start API-level macro recording."""
    return recorder.start(req.name, req.url)


@router.post("/cdp/macro/record/stop")
async def macro_record_stop():
    """Stop API-level recording and save macro."""
    return await recorder.stop()


@router.get("/cdp/macro/record/status")
async def macro_record_status():
    """Get current recording state."""
    return recorder.status()


# ─── Macro Recording (Browser-level) ───

@router.post("/cdp/macro/browser/start")
async def macro_browser_start(req: MacroNameRequest):
    """Start browser-level recording by injecting JS event listeners."""
    return await recorder.start_browser(req.name, req.url)


@router.post("/cdp/macro/browser/stop")
async def macro_browser_stop():
    """Stop browser-level recording, poll events, save macro."""
    return await recorder.stop_browser()


# ─── Macro Playback & CRUD ───

@router.post("/cdp/macro/play")
async def macro_play(req: MacroPlayRequest):
    """Replay a saved macro with timing."""
    return await recorder.play(req.macro_id)


@router.get("/cdp/macro/list")
async def macro_list():
    """List all saved macros."""
    return {"macros": await recorder.list_macros()}


@router.get("/cdp/macro/{macro_id}")
async def macro_get(macro_id: int):
    """Get macro details."""
    macro = await recorder.get_macro(macro_id)
    if not macro:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Macro not found")
    return macro


@router.delete("/cdp/macro/{macro_id}")
async def macro_delete(macro_id: int):
    """Delete a macro."""
    return await recorder.delete_macro(macro_id)


# ─── Screen Recording ───

@router.post("/cdp/screen/record/start")
async def screen_record_start(req: ScreenRecordRequest):
    """Start screen recording via CDP screencast."""
    return await screen_recorder.start(fps=req.fps, quality=req.quality)


@router.post("/cdp/screen/record/stop")
async def screen_record_stop():
    """Stop screen recording and return compiled video."""
    return await screen_recorder.stop()


@router.get("/cdp/screen/record/status")
async def screen_record_status():
    """Get current screen recording state."""
    return screen_recorder.status()


# ─── YouTube Automation ───

@router.post("/cdp/youtube")
async def youtube_play(req: YouTubeRequest):
    """Search YouTube and navigate to the first video result."""
    import httpx
    import json as _json
    from urllib.parse import quote

    query = req.query
    search_url = f"https://www.youtube.com/results?search_query={quote(query)}"

    # Navigate to YouTube search
    await cdp.navigate(search_url)

    # Extract first video link — retry with increasing waits for dynamic content
    extract_js = """
        (function() {
            const links = document.querySelectorAll('a#video-title, a.yt-simple-endpoint[href*="/watch"], a[href*="/watch"]');
            for (const link of links) {
                const href = link.getAttribute('href');
                if (href && href.includes('/watch')) {
                    return href.startsWith('http') ? href : 'https://www.youtube.com' + href;
                }
            }
            return null;
        })()
    """
    video_url = None
    for wait in [2, 3, 5]:
        await asyncio.sleep(wait)
        video_url = await cdp.evaluate(extract_js)
        if video_url:
            break

    if not video_url:
        return {"error": f"No video found for: {query}", "search_url": search_url}

    # Navigate to the video
    if not video_url.startswith("http"):
        video_url = f"https://www.youtube.com{video_url}"

    await cdp.navigate(video_url)

    # Extract video_id for iframe embedding
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(video_url)
    video_id = parse_qs(parsed.query).get("v", [None])[0] or parsed.path.split("/")[-1]

    return {
        "status": "playing",
        "query": query,
        "video_url": video_url,
        "video_id": video_id,
    }


# ─── Live Viewport Stream ───

@router.get("/cdp/viewport/stream")
async def viewport_stream(fps: int = Query(8), quality: int = Query(60)):
    """MJPEG live stream of Chrome's viewport."""
    from fastapi.responses import StreamingResponse
    from core.live_stream import mjpeg_frames

    boundary = "alphabetty_frame"

    async def generate():
        async for frame in mjpeg_frames(fps=fps, quality=quality):
            yield (
                f"--{boundary}\r\n"
                f"Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(frame)}\r\n\r\n"
            ).encode()
            yield frame
            yield b"\r\n"

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=alphabetty_frame",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
