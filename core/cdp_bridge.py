"""CDP Bridge — WebSocket client for Chrome DevTools Protocol.

Connects to the undetected Chrome instance launched by core/chrome.py.
Adds stealth JS injection and human-like mouse/keyboard simulation.
"""

import asyncio
import json
import logging
import random
from typing import Any

import httpx
import websockets

from config import settings

logger = logging.getLogger(__name__)

# Macro recorder hook — set by core.macro when recording is active
_recorder = None

# Stealth JS injected on every page load via Page.addScriptToEvaluateOnNewDocument
STEALTH_JS = """
// navigator.webdriver = undefined (not false — undefined is more natural)
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// Realistic plugins array
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [
            {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
            {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
            {name: 'Native Client', filename: 'internal-nacl-plugin'},
        ];
        arr.refresh = () => {};
        return arr;
    },
});

// Languages
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});

// Chrome runtime
if (!window.chrome) window.chrome = {};
window.chrome.runtime = {};
window.chrome.loadTimes = function() { return {}; };
window.chrome.csi = function() { return {}; };
window.chrome.app = { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } };

// Permissions
const origQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (params) =>
    params.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : origQuery(params);

// WebGL fingerprint spoofing
const glParam = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return glParam.call(this, p);
};

// Remove iframe contentWindow detection
const origGetter = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow').get;
Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
    get: function() { return origGetter.call(this); }
});

// Override toString for detectable functions
const nativeToString = Function.prototype.toString;
const patchedFunctions = new Map();
function patchToString(fn, str) { patchedFunctions.set(fn, str); }
const origToStr = Function.prototype.toString;
Function.prototype.toString = function() {
    if (patchedFunctions.has(this)) return patchedFunctions.get(this);
    return origToStr.call(this);
};
"""


class CDPBridge:
    """Manages CDP WebSocket connections to the undetected Chrome instance."""

    def __init__(self, cdp_url: str = None):
        self.cdp_url = cdp_url or settings.cdp_url
        self._msg_id = 0
        self._stealth_injected = False

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def get_tabs(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.cdp_url}/json/list")
            resp.raise_for_status()
            return resp.json()

    async def get_ws_url(self, tab_id: str = None) -> str:
        tabs = await self.get_tabs()
        if tab_id:
            for tab in tabs:
                if tab.get("id") == tab_id:
                    return tab["webSocketDebuggerUrl"]
        for tab in tabs:
            if tab.get("type") == "page":
                return tab["webSocketDebuggerUrl"]
        return tabs[0]["webSocketDebuggerUrl"]

    async def send_command(self, method: str, params: dict = None,
                           tab_id: str = None) -> dict:
        ws_url = await self.get_ws_url(tab_id)
        async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
            # Inject stealth JS on first connection to a page
            if not self._stealth_injected and "page" in ws_url:
                try:
                    stealth_id = self._next_id()
                    await ws.send(json.dumps({
                        "id": stealth_id,
                        "method": "Page.addScriptToEvaluateOnNewDocument",
                        "params": {"source": STEALTH_JS},
                    }))
                    await ws.recv()  # consume response
                    self._stealth_injected = True
                    logger.info("Stealth JS injected via CDP")
                except Exception as e:
                    logger.warning(f"Stealth injection via CDP failed: {e}")

            msg_id = self._next_id()
            payload = {"id": msg_id, "method": method}
            if params:
                payload["params"] = params
            await ws.send(json.dumps(payload))
            resp = json.loads(await ws.recv())
            return resp.get("result", resp)

    async def navigate(self, url: str, tab_id: str = None) -> dict:
        """Navigate — resets stealth flag so it gets re-injected."""
        if _recorder and _recorder.recording:
            _recorder.record_step("navigate", {"url": url})
        self._stealth_injected = False
        result = await self.send_command("Page.navigate", {"url": url}, tab_id=tab_id)
        from core.events import emit
        emit("page.loaded", {"url": url})
        return result

    async def get_content(self, tab_id: str = None) -> str:
        result = await self.send_command(
            "Runtime.evaluate",
            {"expression": "document.body.innerText", "returnByValue": True},
            tab_id=tab_id,
        )
        return result.get("result", {}).get("value", "")

    async def get_dom(self, depth: int = 3, tab_id: str = None) -> dict:
        doc = await self.send_command("DOM.getDocument", {"depth": depth}, tab_id=tab_id)
        return doc.get("root", {})

    async def query_selector(self, selector: str, tab_id: str = None) -> int:
        doc = await self.send_command("DOM.getDocument", {"depth": 0}, tab_id=tab_id)
        node_id = doc.get("root", {}).get("nodeId", 0)
        result = await self.send_command(
            "DOM.querySelector",
            {"nodeId": node_id, "selector": selector},
            tab_id=tab_id,
        )
        return result.get("nodeId", 0)

    async def evaluate(self, expression: str, tab_id: str = None) -> Any:
        if _recorder and _recorder.recording:
            _recorder.record_step("evaluate", {"expression": expression})
        result = await self.send_command(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
            tab_id=tab_id,
        )
        return result.get("result", {}).get("value")

    async def click(self, selector: str, tab_id: str = None) -> dict:
        """Human-like click with slight random offset and movement.
        Falls back to JS click() for shadow DOM elements."""
        if _recorder and _recorder.recording:
            _recorder.record_step("click", {"selector": selector})
        node_id = await self.query_selector(selector, tab_id=tab_id)
        if not node_id:
            # Fallback: try JS click for shadow DOM / dynamic elements
            js_result = await self.evaluate(
                f'(function(){{ const el = document.querySelector("{selector}"); if(el){{ el.click(); return "clicked"; }} return null; }})()',
                tab_id=tab_id,
            )
            if js_result == "clicked":
                return {"status": "clicked_js", "selector": selector}
            return {"error": "Element not found"}
        box = await self.send_command("DOM.getBoxModel", {"nodeId": node_id}, tab_id=tab_id)
        content = box.get("model", {}).get("content", [])
        if len(content) < 8:
            return {"error": "Could not determine element position"}

        # Calculate center with slight random offset (human-like)
        cx = (content[0] + content[2] + content[4] + content[6]) / 4
        cy = (content[1] + content[3] + content[5] + content[7]) / 4
        x = cx + random.uniform(-3, 3)
        y = cy + random.uniform(-3, 3)

        # Human-like mouse movement — dispatch mouseMoved events toward target
        await self.send_command("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y,
        }, tab_id=tab_id)
        # Tiny delay between move and click
        await asyncio.sleep(random.uniform(0.02, 0.08))

        # Click with realistic timing
        await self.send_command("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        }, tab_id=tab_id)
        await asyncio.sleep(random.uniform(0.03, 0.12))
        await self.send_command("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        }, tab_id=tab_id)

        return {"status": "clicked", "x": round(x, 1), "y": round(y, 1)}

    async def type_text(self, selector: str, text: str, tab_id: str = None) -> dict:
        """Type text into an element. For contenteditable/ProseMirror, uses JS injection
        to avoid doubled characters from keyDown+char events."""
        if _recorder and _recorder.recording:
            _recorder.record_step("type", {"selector": selector, "text": text})
        node_id = await self.query_selector(selector, tab_id=tab_id)

        # Check if element is contenteditable (ProseMirror etc.)
        is_contenteditable = await self.evaluate(
            f'(document.querySelector("{selector}")?.contentEditable === "true") || false',
            tab_id=tab_id,
        )

        if is_contenteditable:
            # Use JS injection for contenteditable — avoids doubled chars
            escaped = text.replace("\\", "\\\\").replace('"', '\\"')
            await self.evaluate(
                f'(function(){{ const el = document.querySelector("{selector}"); if(!el) return "not found"; el.focus(); '
                f'document.execCommand("insertText", false, "{escaped}"); '
                f'el.dispatchEvent(new Event("input", {{bubbles: true}})); return "done"; }})()',
                tab_id=tab_id,
            )
            return {"status": "typed", "text": text}

        # Standard input/textarea — use CDP keystroke events
        if node_id:
            await self.send_command("DOM.focus", {"nodeId": node_id}, tab_id=tab_id)

        for char in text:
            await self.send_command("Input.dispatchKeyEvent", {
                "type": "keyDown", "text": char,
            }, tab_id=tab_id)
            await self.send_command("Input.dispatchKeyEvent", {
                "type": "char", "text": char,
            }, tab_id=tab_id)
            await asyncio.sleep(random.uniform(0.03, 0.12))
            await self.send_command("Input.dispatchKeyEvent", {
                "type": "keyUp", "text": char,
            }, tab_id=tab_id)

        return {"status": "typed", "text": text}

    async def scroll(self, x: int = 0, y: int = 300, tab_id: str = None) -> dict:
        """Human-like scroll."""
        if _recorder and _recorder.recording:
            _recorder.record_step("scroll", {"x": x, "y": y})
        await self.send_command("Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": 0, "y": 0,
            "deltaX": x, "deltaY": y,
        }, tab_id=tab_id)
        return {"status": "scrolled", "x": x, "y": y}

    # ─── Tab management ───

    async def create_tab(self, url: str = "about:blank") -> dict:
        """Open a new Chrome tab and optionally navigate to URL."""
        result = await self.send_command("Target.createTarget", {"url": url})
        target_id = result.get("targetId")
        return {"status": "created", "targetId": target_id, "url": url}

    async def close_tab(self, target_id: str) -> dict:
        """Close a Chrome tab by target ID."""
        await self.send_command("Target.closeTarget", {"targetId": target_id})
        return {"status": "closed", "targetId": target_id}

    async def activate_tab(self, target_id: str) -> dict:
        """Activate (focus) a Chrome tab by target ID."""
        await self.send_command("Target.activateTarget", {"targetId": target_id})
        return {"status": "activated", "targetId": target_id}

    # ─── Wait / PDF ───

    async def wait_for_selector(self, selector: str, timeout: int = 10000, tab_id: str = None) -> dict:
        """Poll until an element matching the selector appears. Returns when found or timeout.
        Uses both DOM.querySelector and JS querySelector for shadow DOM support."""
        interval = 0.3
        elapsed = 0.0
        while elapsed < timeout / 1000.0:
            node_id = await self.query_selector(selector, tab_id=tab_id)
            if node_id:
                return {"status": "found", "selector": selector, "waited_ms": int(elapsed * 1000)}
            # Fallback: JS querySelector (works with shadow DOM / dynamic content)
            js_found = await self.evaluate(
                f'document.querySelector("{selector}") !== null',
                tab_id=tab_id,
            )
            if js_found:
                return {"status": "found_js", "selector": selector, "waited_ms": int(elapsed * 1000)}
            await asyncio.sleep(interval)
            elapsed += interval
        return {"status": "timeout", "selector": selector, "timeout_ms": timeout}

    async def print_pdf(self, tab_id: str = None) -> dict:
        """Print the current page as a PDF (base64-encoded)."""
        import base64
        result = await self.send_command("Page.printToPDF", {
            "printBackground": True,
            "paperWidth": 8.5,
            "paperHeight": 11,
        }, tab_id=tab_id)
        pdf_b64 = result.get("data", "")
        return {"status": "ok", "pdf_base64": pdf_b64[:100] + "...", "size_bytes": len(base64.b64decode(pdf_b64))}

    async def screenshot(self, format: str = "png", tab_id: str = None) -> bytes:
        import base64
        result = await self.send_command("Page.captureScreenshot", {"format": format}, tab_id=tab_id)
        return base64.b64decode(result.get("data", ""))

    async def get_cookies(self, tab_id: str = None) -> list[dict]:
        result = await self.send_command("Network.getAllCookies", tab_id=tab_id)
        return result.get("cookies", [])

    async def set_cookie(self, name: str, value: str, domain: str, path: str = "/",
                         tab_id: str = None) -> dict:
        return await self.send_command("Network.setCookie", {
            "name": name, "value": value, "domain": domain, "path": path,
        }, tab_id=tab_id)

    async def user_agent(self, tab_id: str = None) -> str:
        return await self.evaluate("navigator.userAgent", tab_id=tab_id)

    async def is_undetected(self, tab_id: str = None) -> dict:
        """Check key anti-detection vectors."""
        checks = await self.evaluate("""
            JSON.stringify({
                webdriver: navigator.webdriver,
                plugins: navigator.plugins.length,
                languages: navigator.languages,
                chrome: !!window.chrome,
                userAgent: navigator.userAgent.substring(0, 80),
            })
        """)
        try:
            return json.loads(checks) if isinstance(checks, str) else checks
        except (json.JSONDecodeError, TypeError):
            return {"raw": checks}


# Global instance
cdp = CDPBridge()
