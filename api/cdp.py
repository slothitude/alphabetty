import json
import logging
import asyncio
import random
from functools import wraps
from typing import Optional

import httpx
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

from config import settings
from core.auth import get_current_user
from core.cdp_bridge import cdp
from core.macro import recorder
from core.recording import screen_recorder
from models.user import User

router = APIRouter(tags=["cdp"])
logger = logging.getLogger(__name__)


# ─── Structured CDP error handling ───

def cdp_handler(func):
    """Decorator that catches CDP exceptions and returns structured error codes.
    Also enforces per-user rate limiting (60 req/min on CDP endpoints)."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Rate limit check — extract user from kwargs (dependency injection)
        user = kwargs.get("user")
        if user and _rate_limited(f"cdp:{user.id}"):
            raise HTTPException(status_code=429, detail={"error": "RATE_LIMITED", "message": "Too many CDP requests. Max 60/min."})

        try:
            result = await func(*args, **kwargs)
            # Check for error dicts returned by CDP bridge methods
            if isinstance(result, dict) and "error" in result and "status" not in result:
                error_msg = result["error"]
                code = "ELEMENT_NOT_FOUND" if "not found" in error_msg.lower() else "CDP_ERROR"
                raise HTTPException(status_code=400, detail={"error": code, "message": error_msg})
            return result
        except HTTPException:
            raise
        except TimeoutError as e:
            raise HTTPException(status_code=504, detail={"error": "CDP_TIMEOUT", "message": str(e)})
        except (ConnectionError, httpx.ConnectError, websockets.exceptions.WebSocketException) as e:
            raise HTTPException(status_code=503, detail={"error": "CHROME_OFFLINE", "message": str(e)})
        except Exception as e:
            logger.error(f"CDP error in {func.__name__}: {e}")
            raise HTTPException(status_code=500, detail={"error": "CDP_ERROR", "message": str(e)})
    return wrapper


# ─── In-memory rate limiter ───

import time
from collections import defaultdict

_rate_windows: dict[str, list[float]] = defaultdict(list)

def _rate_limited(key: str, max_requests: int = 60, window_sec: int = 60) -> bool:
    """Returns True if rate limit exceeded."""
    now = time.time()
    cutoff = now - window_sec
    _rate_windows[key] = [t for t in _rate_windows[key] if t > cutoff]
    if len(_rate_windows[key]) >= max_requests:
        return True
    _rate_windows[key].append(now)
    return False


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
    variables: dict = {}          # {{variable}} substitutions
    on_error: str = "continue"    # "continue" or "abort"


class ScreenRecordRequest(BaseModel):
    fps: int = 10
    quality: int = 80


class YouTubeRequest(BaseModel):
    query: str


class TabUrlRequest(BaseModel):
    url: str = "about:blank"


class TabIdRequest(BaseModel):
    target_id: str


class RawCDPRequest(BaseModel):
    method: str
    params: dict = {}
    tab_id: Optional[str] = None


class InsertTextRequest(BaseModel):
    text: str
    tab_id: Optional[str] = None


class ClickAtRequest(BaseModel):
    x: float
    y: float
    tab_id: Optional[str] = None


class IframeRequest(BaseModel):
    selector: str
    iframe_selector: str = "iframe"
    tab_id: Optional[str] = None


class IframeTypeRequest(BaseModel):
    selector: str
    text: str
    iframe_selector: str = "iframe"
    tab_id: Optional[str] = None


class UploadURLRequest(BaseModel):
    selector: str
    file_url: str
    tab_id: Optional[str] = None


# ─── Tab management ───

@router.get("/cdp/tabs")
async def list_tabs(user: User = Depends(get_current_user)):
    return {"tabs": await cdp.get_tabs()}


@router.post("/cdp/tab/new")
async def create_tab(req: TabUrlRequest, user: User = Depends(get_current_user)):
    """Open a new Chrome tab and navigate to URL."""
    result = await cdp.create_tab(req.url)
    return result


@router.post("/cdp/tab/close")
async def close_tab(req: TabIdRequest, user: User = Depends(get_current_user)):
    """Close a Chrome tab by target ID."""
    result = await cdp.close_tab(req.target_id)
    return result


@router.post("/cdp/tab/activate")
async def activate_tab(req: TabIdRequest, user: User = Depends(get_current_user)):
    """Activate (focus) a Chrome tab by target ID."""
    result = await cdp.activate_tab(req.target_id)
    return result


@router.get("/cdp/print-pdf")
async def print_pdf(tab_id: Optional[str] = None, user: User = Depends(get_current_user)):
    """Print the current page as PDF."""
    result = await cdp.print_pdf(tab_id=tab_id)
    return result


@router.post("/cdp/wait-for")
async def wait_for_element(req: QueryRequest, user: User = Depends(get_current_user)):
    """Wait for an element matching a CSS selector to appear."""
    result = await cdp.wait_for_selector(req.selector, timeout=10000, tab_id=req.tab_id)
    return result


@router.get("/cdp/status")
async def chrome_status(user: User = Depends(get_current_user)):
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


# ─── Chrome Profile management ───

class ProfileNameRequest(BaseModel):
    name: str

@router.get("/cdp/profiles")
async def list_profiles(user: User = Depends(get_current_user)):
    """List available Chrome profiles."""
    import os
    profiles_dir = settings.chrome_profiles_dir
    if not os.path.exists(profiles_dir):
        return {"profiles": ["Default"], "active": "Default"}
    profiles = [d for d in os.listdir(profiles_dir)
                if os.path.isdir(os.path.join(profiles_dir, d))]
    profiles.insert(0, "Default")
    active = os.environ.get("ALPHABETTY_ACTIVE_PROFILE", "Default")
    return {"profiles": profiles, "active": active}


@router.post("/cdp/profiles/create")
async def create_profile(req: ProfileNameRequest, user: User = Depends(get_current_user)):
    """Create a new Chrome profile directory."""
    import os
    name = req.name.replace("/", "").replace("\\", "").replace("..", "")
    if not name or name == "Default":
        raise HTTPException(400, "Invalid profile name")
    profile_path = os.path.join(settings.chrome_profiles_dir, name)
    os.makedirs(profile_path, exist_ok=True)
    return {"status": "created", "profile": name, "path": profile_path}


@router.delete("/cdp/profiles/{name}")
async def delete_profile(name: str, user: User = Depends(get_current_user)):
    """Delete a Chrome profile directory."""
    import os, shutil
    name = name.replace("/", "").replace("\\", "").replace("..", "")
    if name == "Default":
        raise HTTPException(400, "Cannot delete Default profile")
    profile_path = os.path.join(settings.chrome_profiles_dir, name)
    if os.path.exists(profile_path):
        shutil.rmtree(profile_path)
        return {"status": "deleted", "profile": name}
    raise HTTPException(404, "Profile not found")


# ─── Navigation ───

@router.post("/cdp/navigate")
@cdp_handler
async def navigate(req: NavigateRequest, user: User = Depends(get_current_user)):
    result = await cdp.navigate(req.url, tab_id=req.tab_id)
    return {"status": "ok", "result": result}


@router.get("/cdp/content")
async def get_content(tab_id: Optional[str] = None, user: User = Depends(get_current_user)):
    text = await cdp.get_content(tab_id=tab_id)
    return {"content": text}


@router.get("/cdp/dom")
async def get_dom(tab_id: Optional[str] = None, depth: int = Query(3), user: User = Depends(get_current_user)):
    dom = await cdp.get_dom(depth, tab_id=tab_id)
    return {"dom": dom}


# ─── DOM queries ───

@router.post("/cdp/query")
async def query_selector(req: QueryRequest, user: User = Depends(get_current_user)):
    node_id = await cdp.query_selector(req.selector, tab_id=req.tab_id)
    return {"nodeId": node_id}


@router.post("/cdp/evaluate")
async def evaluate(req: EvaluateRequest, user: User = Depends(get_current_user)):
    result = await cdp.evaluate(req.expression, tab_id=req.tab_id)
    return {"result": result}


# ─── Human-like interactions ───

@router.post("/cdp/click")
@cdp_handler
async def click_element(req: ClickRequest, user: User = Depends(get_current_user)):
    """Human-like click with random offset and timing."""
    result = await cdp.click(req.selector, tab_id=req.tab_id)
    return result


@router.post("/cdp/type")
@cdp_handler
async def type_text(req: TypeRequest, user: User = Depends(get_current_user)):
    """Human-like typing with random delays between keystrokes."""
    result = await cdp.type_text(req.selector, req.text, tab_id=req.tab_id)
    return result


@router.post("/cdp/scroll")
@cdp_handler
async def scroll_page(req: ScrollRequest, user: User = Depends(get_current_user)):
    """Human-like scroll."""
    result = await cdp.scroll(req.x, req.y, tab_id=req.tab_id)
    return result


# ─── Fast / Raw CDP ───

@router.post("/cdp/send")
@cdp_handler
async def raw_cdp(req: RawCDPRequest, user: User = Depends(get_current_user)):
    """Send any raw CDP protocol command directly."""
    result = await cdp.send_raw(req.method, req.params, tab_id=req.tab_id)
    return {"result": result}


@router.post("/cdp/insert-text")
@cdp_handler
async def insert_text(req: InsertTextRequest, user: User = Depends(get_current_user)):
    """Insert text at cursor natively via CDP. Triggers all browser events — works with React/Vue.
    Much faster than character-by-character type."""
    result = await cdp.insert_text(req.text, tab_id=req.tab_id)
    return result


@router.post("/cdp/click-at")
@cdp_handler
async def click_at(req: ClickAtRequest, user: User = Depends(get_current_user)):
    """Fast mouse click at exact coordinates. No delays."""
    result = await cdp.click_at(req.x, req.y, tab_id=req.tab_id)
    return result


@router.post("/cdp/click-iframe")
async def click_iframe(req: IframeRequest, user: User = Depends(get_current_user)):
    """Click an element inside an iframe."""
    result = await cdp.click_iframe(req.selector, req.iframe_selector, tab_id=req.tab_id)
    return result


@router.post("/cdp/type-iframe")
async def type_iframe(req: IframeTypeRequest, user: User = Depends(get_current_user)):
    """Focus element inside an iframe and insert text natively."""
    result = await cdp.type_iframe(req.selector, req.text, req.iframe_selector, tab_id=req.tab_id)
    return result


@router.post("/cdp/upload-url")
async def upload_file_url(req: UploadURLRequest, user: User = Depends(get_current_user)):
    """Download a file from URL and upload to a file input. No base64 needed."""
    result = await cdp.upload_file_url(req.selector, req.file_url, tab_id=req.tab_id)
    return result


@router.post("/cdp/upload-file")
@cdp_handler
async def upload_file(
    selector: str = Query(...),
    tab_id: Optional[str] = None,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Upload a local file to a file input via multipart form data."""
    import tempfile, os
    # Save uploaded file to temp location
    suffix = os.path.splitext(file.filename or "file")[1]
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        content = await file.read()
        tmp.write(content)
        tmp.close()
        result = await cdp.upload_local_file(selector, tmp.name, tab_id=tab_id)
    finally:
        os.unlink(tmp.name)
    return result


@router.get("/cdp/screenshot")
@cdp_handler
async def screenshot(tab_id: Optional[str] = None, format: str = Query("png"), user: User = Depends(get_current_user)):
    data = await cdp.screenshot(format, tab_id=tab_id)
    from fastapi.responses import Response
    return Response(content=data, media_type=f"image/{format}")


@router.post("/cdp/extract")
async def extract_data(req: ExtractRequest, user: User = Depends(get_current_user)):
    data = await cdp.evaluate(req.expression)
    return {"data": data}


# ─── Cookies ───

@router.get("/cdp/cookies")
async def get_cookies(tab_id: Optional[str] = None, user: User = Depends(get_current_user)):
    cookies = await cdp.get_cookies(tab_id=tab_id)
    return {"cookies": cookies}


@router.post("/cdp/cookies")
async def set_cookie(req: CookieRequest, user: User = Depends(get_current_user)):
    result = await cdp.set_cookie(req.name, req.value, req.domain, req.path)
    return {"status": "ok", "result": result}


# ─── Macro Recording (API-level) ───

@router.post("/cdp/macro/record/start")
async def macro_record_start(req: MacroNameRequest, user: User = Depends(get_current_user)):
    """Start API-level macro recording."""
    return recorder.start(req.name, req.url)


@router.post("/cdp/macro/record/stop")
async def macro_record_stop(user: User = Depends(get_current_user)):
    """Stop API-level recording and save macro."""
    return await recorder.stop()


@router.get("/cdp/macro/record/status")
async def macro_record_status(user: User = Depends(get_current_user)):
    """Get current recording state."""
    return recorder.status()


# ─── Macro Recording (Browser-level) ───

@router.post("/cdp/macro/browser/start")
async def macro_browser_start(req: MacroNameRequest, user: User = Depends(get_current_user)):
    """Start browser-level recording by injecting JS event listeners."""
    return await recorder.start_browser(req.name, req.url)


@router.post("/cdp/macro/browser/stop")
async def macro_browser_stop(user: User = Depends(get_current_user)):
    """Stop browser-level recording, poll events, save macro."""
    return await recorder.stop_browser()


# ─── Macro Playback & CRUD ───

@router.post("/cdp/macro/play")
async def macro_play(req: MacroPlayRequest, user: User = Depends(get_current_user)):
    """Replay a saved macro with timing, variables, and error recovery."""
    return await recorder.play(req.macro_id, variables=req.variables, on_error=req.on_error)


@router.get("/cdp/macro/list")
async def macro_list(user: User = Depends(get_current_user)):
    """List all saved macros."""
    return {"macros": await recorder.list_macros()}


@router.get("/cdp/macro/{macro_id}")
async def macro_get(macro_id: int, user: User = Depends(get_current_user)):
    """Get macro details."""
    macro = await recorder.get_macro(macro_id)
    if not macro:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Macro not found")
    return macro


@router.delete("/cdp/macro/{macro_id}")
async def macro_delete(macro_id: int, user: User = Depends(get_current_user)):
    """Delete a macro."""
    return await recorder.delete_macro(macro_id)


# ─── Screen Recording ───

@router.post("/cdp/screen/record/start")
async def screen_record_start(req: ScreenRecordRequest, user: User = Depends(get_current_user)):
    """Start screen recording via CDP screencast."""
    return await screen_recorder.start(fps=req.fps, quality=req.quality)


@router.post("/cdp/screen/record/stop")
async def screen_record_stop(user: User = Depends(get_current_user)):
    """Stop screen recording and return compiled video."""
    return await screen_recorder.stop()


@router.get("/cdp/screen/record/status")
async def screen_record_status(user: User = Depends(get_current_user)):
    """Get current screen recording state."""
    return screen_recorder.status()


# ─── YouTube Automation ───

@router.post("/cdp/youtube")
async def youtube_play(req: YouTubeRequest, user: User = Depends(get_current_user)):
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
async def viewport_stream(fps: int = Query(8), quality: int = Query(60), user: User = Depends(get_current_user)):
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
    # Auth check before accepting
    from core.auth import _validate_jwt, _validate_api_key, get_db
    token = ws.cookies.get("access_token")
    user = None
    if token:
        db_gen = get_db()
        db = await anext(db_gen)
        try:
            user = await _validate_jwt(token, db)
        finally:
            await db_gen.aclose()
    if not user:
        # Try query param token (for non-browser clients)
        qp_token = ws.query_params.get("token")
        if qp_token:
            db_gen = get_db()
            db = await anext(db_gen)
            try:
                if qp_token.startswith("alph_"):
                    user = await _validate_api_key(qp_token, db)
                else:
                    user = await _validate_jwt(qp_token, db)
            finally:
                await db_gen.aclose()
    if not user:
        await ws.close(code=4001, reason="Not authenticated")
        return

    await ws.accept()
    cdp_ws = None
    try:
        while True:
            try:
                data = await ws.receive_text()
                msg = json.loads(data)
                # Connect/reconnect CDP WebSocket as needed
                if cdp_ws is None or cdp_ws.closed:
                    ws_url = await cdp.get_ws_url()
                    cdp_ws = await websockets.connect(ws_url, max_size=10 * 1024 * 1024).__aenter__()
                await cdp_ws.send(json.dumps(msg))
                resp = await asyncio.wait_for(cdp_ws.recv(), timeout=30)
                await ws.send_text(resp)
            except (websockets.exceptions.ConnectionClosed, TimeoutError):
                # CDP WS dropped — reconnect on next message
                if cdp_ws and not cdp_ws.closed:
                    try:
                        await cdp_ws.close()
                    except Exception:
                        pass
                cdp_ws = None
                await ws.send_text(json.dumps({"error": "reconnecting", "retry": True}))
            except WebSocketDisconnect:
                break
    except Exception as e:
        logger.error(f"CDP WebSocket error: {e}")
    finally:
        if cdp_ws and not cdp_ws.closed:
            try:
                await cdp_ws.close()
            except Exception:
                pass
