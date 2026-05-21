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

    async def navigate(self, url: str) -> dict:
        """Navigate — resets stealth flag so it gets re-injected."""
        self._stealth_injected = False
        return await self.send_command("Page.navigate", {"url": url})

    async def get_content(self) -> str:
        result = await self.send_command(
            "Runtime.evaluate",
            {"expression": "document.body.innerText", "returnByValue": True},
        )
        return result.get("result", {}).get("value", "")

    async def get_dom(self, depth: int = 3) -> dict:
        doc = await self.send_command("DOM.getDocument", {"depth": depth})
        return doc.get("root", {})

    async def query_selector(self, selector: str) -> int:
        doc = await self.send_command("DOM.getDocument", {"depth": 0})
        node_id = doc.get("root", {}).get("nodeId", 0)
        result = await self.send_command(
            "DOM.querySelector",
            {"nodeId": node_id, "selector": selector},
        )
        return result.get("nodeId", 0)

    async def evaluate(self, expression: str) -> Any:
        result = await self.send_command(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        return result.get("result", {}).get("value")

    async def click(self, selector: str) -> dict:
        """Human-like click with slight random offset and movement."""
        node_id = await self.query_selector(selector)
        if not node_id:
            return {"error": "Element not found"}
        box = await self.send_command("DOM.getBoxModel", {"nodeId": node_id})
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
        })
        # Tiny delay between move and click
        await asyncio.sleep(random.uniform(0.02, 0.08))

        # Click with realistic timing
        await self.send_command("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
        await asyncio.sleep(random.uniform(0.03, 0.12))
        await self.send_command("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })

        return {"status": "clicked", "x": round(x, 1), "y": round(y, 1)}

    async def type_text(self, selector: str, text: str) -> dict:
        """Human-like typing with random delays between keystrokes."""
        node_id = await self.query_selector(selector)
        if node_id:
            await self.send_command("DOM.focus", {"nodeId": node_id})

        for char in text:
            await self.send_command("Input.dispatchKeyEvent", {
                "type": "keyDown", "text": char,
            })
            await self.send_command("Input.dispatchKeyEvent", {
                "type": "char", "text": char,
            })
            # Random delay between keystrokes (30-120ms, human-like)
            await asyncio.sleep(random.uniform(0.03, 0.12))
            await self.send_command("Input.dispatchKeyEvent", {
                "type": "keyUp", "text": char,
            })

        return {"status": "typed", "text": text}

    async def scroll(self, x: int = 0, y: int = 300) -> dict:
        """Human-like scroll."""
        await self.send_command("Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": 0, "y": 0,
            "deltaX": x, "deltaY": y,
        })
        return {"status": "scrolled", "x": x, "y": y}

    async def screenshot(self, format: str = "png") -> bytes:
        import base64
        result = await self.send_command("Page.captureScreenshot", {"format": format})
        return base64.b64decode(result.get("data", ""))

    async def get_cookies(self) -> list[dict]:
        result = await self.send_command("Network.getAllCookies")
        return result.get("cookies", [])

    async def set_cookie(self, name: str, value: str, domain: str, path: str = "/") -> dict:
        return await self.send_command("Network.setCookie", {
            "name": name, "value": value, "domain": domain, "path": path,
        })

    async def user_agent(self) -> str:
        return await self.evaluate("navigator.userAgent")

    async def is_undetected(self) -> dict:
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
