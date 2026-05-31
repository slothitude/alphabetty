"""CDP Bridge — WebSocket client for Chrome DevTools Protocol.

Connects to the undetected Chrome instance launched by core/chrome.py.
Adds stealth JS injection, human-like mouse/keyboard simulation,
smart navigation wait, structured extraction, and obstacle detection.
"""

import asyncio
import json
import logging
import random
import time
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
import websockets

from config import settings

logger = logging.getLogger(__name__)

# Macro recorder hook — set by core.macro when recording is active
_recorder = None

# ─── Keyboard key mapping for press_key() ───

_KEY_MAP = {
    "enter":     {"key": "Enter",      "code": "Enter",      "windowsVirtualKeyCode": 13},
    "return":    {"key": "Enter",      "code": "Enter",      "windowsVirtualKeyCode": 13},
    "tab":       {"key": "Tab",        "code": "Tab",        "windowsVirtualKeyCode": 9},
    "escape":    {"key": "Escape",     "code": "Escape",     "windowsVirtualKeyCode": 27},
    "esc":       {"key": "Escape",     "code": "Escape",     "windowsVirtualKeyCode": 27},
    "backspace": {"key": "Backspace",  "code": "Backspace",  "windowsVirtualKeyCode": 8},
    "delete":    {"key": "Delete",     "code": "Delete",     "windowsVirtualKeyCode": 46},
    "insert":    {"key": "Insert",     "code": "Insert",     "windowsVirtualKeyCode": 45},
    "home":      {"key": "Home",       "code": "Home",       "windowsVirtualKeyCode": 36},
    "end":       {"key": "End",        "code": "End",        "windowsVirtualKeyCode": 35},
    "pageup":    {"key": "PageUp",     "code": "PageUp",     "windowsVirtualKeyCode": 33},
    "pagedown":  {"key": "PageDown",   "code": "PageDown",   "windowsVirtualKeyCode": 34},
    "arrowup":   {"key": "ArrowUp",    "code": "ArrowUp",    "windowsVirtualKeyCode": 38},
    "arrowdown": {"key": "ArrowDown",  "code": "ArrowDown",  "windowsVirtualKeyCode": 40},
    "arrowleft": {"key": "ArrowLeft",  "code": "ArrowLeft",  "windowsVirtualKeyCode": 37},
    "arrowright":{"key": "ArrowRight", "code": "ArrowRight", "windowsVirtualKeyCode": 39},
    "up":        {"key": "ArrowUp",    "code": "ArrowUp",    "windowsVirtualKeyCode": 38},
    "down":      {"key": "ArrowDown",  "code": "ArrowDown",  "windowsVirtualKeyCode": 40},
    "left":      {"key": "ArrowLeft",  "code": "ArrowLeft",  "windowsVirtualKeyCode": 37},
    "right":     {"key": "ArrowRight", "code": "ArrowRight", "windowsVirtualKeyCode": 39},
    "space":     {"key": " ",          "code": "Space",      "windowsVirtualKeyCode": 32},
    "capslock":  {"key": "CapsLock",   "code": "CapsLock",   "windowsVirtualKeyCode": 20},
    "f1":  {"key": "F1",  "code": "F1",  "windowsVirtualKeyCode": 112},
    "f2":  {"key": "F2",  "code": "F2",  "windowsVirtualKeyCode": 113},
    "f3":  {"key": "F3",  "code": "F3",  "windowsVirtualKeyCode": 114},
    "f4":  {"key": "F4",  "code": "F4",  "windowsVirtualKeyCode": 115},
    "f5":  {"key": "F5",  "code": "F5",  "windowsVirtualKeyCode": 116},
    "f6":  {"key": "F6",  "code": "F6",  "windowsVirtualKeyCode": 117},
    "f7":  {"key": "F7",  "code": "F7",  "windowsVirtualKeyCode": 118},
    "f8":  {"key": "F8",  "code": "F8",  "windowsVirtualKeyCode": 119},
    "f9":  {"key": "F9",  "code": "F9",  "windowsVirtualKeyCode": 120},
    "f10": {"key": "F10", "code": "F10", "windowsVirtualKeyCode": 121},
    "f11": {"key": "F11", "code": "F11", "windowsVirtualKeyCode": 122},
    "f12": {"key": "F12", "code": "F12", "windowsVirtualKeyCode": 123},
}

_MODIFIER_MAP = {
    "ctrl":  2,
    "control": 2,
    "shift": 8,
    "alt":   1,
    "meta":  4,
    "cmd":   4,
    "command": 4,
}

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

// Canvas fingerprint noise
const origGetContext = HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext = function(type, attrs) {
    const ctx = origGetContext.call(this, type, attrs);
    if (type === '2d') {
        const origGetImageData = ctx.getImageData.bind(ctx);
        ctx.getImageData = function(x, y, w, h) {
            const imageData = origGetImageData(x, y, w, h);
            // Subtle pixel XOR on random pixels
            for (let i = 0; i < imageData.data.length; i += 4) {
                if (Math.random() > 0.98) {
                    imageData.data[i] ^= 1;
                }
            }
            return imageData;
        };
    }
    return ctx;
};

// navigator.hardwareConcurrency
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});

// navigator.platform
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});

// Screen consistency
Object.defineProperty(screen, 'width', {get: () => 1920});
Object.defineProperty(screen, 'height', {get: () => 1080});
Object.defineProperty(screen, 'availWidth', {get: () => 1920});
Object.defineProperty(screen, 'availHeight', {get: () => 1040});
Object.defineProperty(screen, 'colorDepth', {get: () => 24});
"""


# ─── Structured Extraction JS ───

EXTRACT_STRUCTURED_JS = """
(async () => {
    const meta = {};

    // Open Graph metadata
    const getMeta = (selectors) => {
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el) return el.getAttribute('content') || el.textContent;
        }
        return null;
    };
    meta.title = getMeta(['meta[property="og:title"]', 'title']);
    meta.description = getMeta(['meta[property="og:description"]', 'meta[name="description"]']);
    meta.image = getMeta(['meta[property="og:image"]']);
    meta.author = getMeta(['meta[name="author"]']);
    meta.robots = getMeta(['meta[name="robots"]']);

    // Schema.org JSON-LD
    const schemas = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach(el => {
        try { schemas.push(JSON.parse(el.textContent)); } catch(e) {}
    });

    // Headings hierarchy
    const headings = [];
    document.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(el => {
        headings.push({level: parseInt(el.tagName[1]), text: el.textContent.trim().substring(0, 200), id: el.id || null});
    });

    // Links (top 50)
    const links = [];
    document.querySelectorAll('a[href]').forEach(el => {
        if (links.length >= 50) return;
        const href = el.getAttribute('href');
        const text = el.textContent.trim();
        if (href && !href.startsWith('#') && !href.startsWith('javascript:') && text) {
            links.push({text: text.substring(0, 100), href: href.substring(0, 500)});
        }
    });

    // Images (top 20)
    const images = [];
    document.querySelectorAll('img').forEach(el => {
        if (images.length >= 20) return;
        const src = el.getAttribute('src') || el.getAttribute('data-src');
        if (src) {
            images.push({src: src.substring(0, 500), alt: (el.alt || '').substring(0, 100), width: el.naturalWidth || 0, height: el.naturalHeight || 0});
        }
    });

    // Tables (up to 3)
    const tables = [];
    document.querySelectorAll('table').forEach((table, idx) => {
        if (idx >= 3) return;
        const rows = [];
        const headerCells = [];
        table.querySelectorAll('thead th, tr:first-child td, tr:first-child th').forEach(cell => {
            headerCells.push(cell.textContent.trim().substring(0, 100));
        });
        table.querySelectorAll('tbody tr, tr').forEach((row, ri) => {
            if (headerCells.length > 0 && ri === 0) return;
            const cells = [];
            row.querySelectorAll('td').forEach(cell => {
                cells.push(cell.textContent.trim().substring(0, 500));
            });
            if (cells.length > 0) {
                const obj = {};
                headerCells.forEach((h, i) => { obj[h] = cells[i] || ''; });
                rows.push(obj);
            }
        });
        if (rows.length > 0) tables.push(rows);
    });

    // Page type classification
    const url = location.href.toLowerCase();
    const bodyText = document.body?.innerText?.toLowerCase() || '';
    const title = document.title?.toLowerCase() || '';
    let page_type = 'content';
    if (/login|signin|sign-in|auth/i.test(url + title)) page_type = 'auth';
    else if (/search|google|bing|duckduckgo/i.test(url)) page_type = 'search';
    else if (/(article|post|blog|story|news)/.test(url + title)) page_type = 'article';
    else if (/shop|cart|checkout|product|amazon|ebay/i.test(url + title)) page_type = 'ecommerce';
    else if (/dashboard|admin|panel|settings/.test(url + title)) page_type = 'dashboard';
    else if (/youtube|vimeo|twitch|video/i.test(url + title)) page_type = 'video';
    else if (/register|signup|create.account/.test(url + title)) page_type = 'form';
    else if (document.querySelector('input[type="email"], input[type="password"]')) page_type = 'auth';

    // Word count and language
    const wordCount = bodyText.split(/\\s+/).filter(w => w.length > 0).length;
    const lang = document.documentElement.lang || 'unknown';

    return {
        meta, schemas: schemas.slice(0, 3), headings, links, images, tables,
        page_type, word_count: wordCount, language: lang,
        url: location.href,
        document_title: document.title || '',
    };
})()
"""


# ─── Obstacle Detection JS ───

OBSTACLE_DETECT_JS = """
(() => {
    const obstacles = {};

    // Cookie banner detection
    const cookieSels = [
        '[class*="cookie" i]', '[id*="cookie" i]', '[id*="consent" i]',
        '#onetrust-banner', '#onetrust-consent-sdk', '.cc-banner',
        '[class*="cookie-consent" i]', '[class*="cookiebanner" i]',
        '[class*="CookieBanner" i]', '[class*="consent-banner" i]',
        '[aria-label*="cookie" i]', '[aria-label*="consent" i]',
    ];
    for (const sel of cookieSels) {
        const el = document.querySelector(sel);
        if (el && el.offsetHeight > 0) {
            obstacles.cookie_banner = sel;
            break;
        }
    }

    // Paywall detection
    const bodyText = document.body?.innerText?.substring(0, 5000) || '';
    if (/subscribe|paywall|premium|sign in to continue|log in to continue/i.test(bodyText)) {
        if (document.querySelector('[class*="paywall" i], [class*="subscribe" i], [class*="premium" i]')) {
            obstacles.paywall = 'detected';
        }
    }

    // CAPTCHA detection
    if (document.querySelector('.g-recaptcha, iframe[src*="recaptcha"], .h-captcha, iframe[src*="hcaptcha"], [class*="captcha" i]')) {
        obstacles.captcha = 'detected';
    }

    // Cloudflare challenge
    if (document.querySelector('#challenge-running, .cf-browser-verification, #cf-challenge-running')) {
        obstacles.cloudflare = 'detected';
    }
    if (/just a moment|checking your browser|cloudflare/i.test(document.title || '')) {
        obstacles.cloudflare = 'title_check';
    }

    // Popup/modal detection
    const dialogs = document.querySelectorAll('[role="dialog"], [class*="modal" i], [class*="popup" i]');
    for (const d of dialogs) {
        const rect = d.getBoundingClientRect();
        if (rect.width > 200 && rect.height > 200 && rect.width < window.innerWidth * 0.9) {
            obstacles.popup = 'detected';
            break;
        }
    }

    // Login wall detection
    if (/sign in to read|log in to continue|login required/i.test(bodyText)) {
        obstacles.login_wall = 'detected';
    }

    return obstacles;
})()
"""


# ─── Obstacle Dismissal JS ───

OBSTACLE_DISMISS_COOKIE_JS = """
(() => {
    const btnSels = [
        'button[class*="accept" i]', 'button[class*="agree" i]',
        'button[class*="dismiss" i]', 'button[class*="close" i]',
        'a[class*="accept" i]', 'a[class*="agree" i]',
        '[class*="cookie"] button', '[id*="cookie"] button',
        '[class*="consent"] button', '[class*="consent"] a',
        '.cc-btn', '.cc-dismiss', '#onetrust-accept-btn-handler',
        'button[aria-label*="accept" i]', 'button[aria-label*="cookie" i]',
    ];
    for (const sel of btnSels) {
        const el = document.querySelector(sel);
        if (el && el.offsetHeight > 0) {
            el.click();
            return 'clicked: ' + sel;
        }
    }
    // Fallback: hide cookie banners
    const hideSels = [
        '[class*="cookie" i]', '[id*="cookie" i]', '[id*="consent" i]',
        '#onetrust-banner', '.cc-banner', '[class*="cookie-consent" i]',
    ];
    for (const sel of hideSels) {
        const el = document.querySelector(sel);
        if (el) {
            el.style.display = 'none';
            return 'hidden: ' + sel;
        }
    }
    return 'none_found';
})()
"""

OBSTACLE_DISMISS_POPUP_JS = """
(() => {
    const closeSels = [
        '[role="dialog"] button[class*="close" i]',
        '[role="dialog"] [aria-label*="close" i]',
        '[class*="modal"] button[class*="close" i]',
        '[class*="popup"] button[class*="close" i]',
        '[role="dialog"] button[aria-label*="dismiss" i]',
        '.modal-close', '.popup-close', '.close-modal',
    ];
    for (const sel of closeSels) {
        const el = document.querySelector(sel);
        if (el && el.offsetHeight > 0) {
            el.click();
            return 'clicked: ' + sel;
        }
    }
    // Fallback: hide popups
    const popups = document.querySelectorAll('[role="dialog"], [class*="modal" i], [class*="popup" i]');
    for (const p of popups) {
        const rect = p.getBoundingClientRect();
        if (rect.width > 200 && rect.height > 200) {
            p.style.display = 'none';
            return 'hidden';
        }
    }
    return 'none_found';
})()
"""


class NavigationError(Exception):
    """Raised when Page.navigate returns an errorText."""
    pass


class CDPBridge:
    """Manages CDP WebSocket connections to the undetected Chrome instance."""

    def __init__(self, cdp_url: str = None):
        self.cdp_url = cdp_url or settings.cdp_url
        self._msg_id = 0
        self._stealth_injected: dict[str, bool] = {}  # Per-tab stealth injection flag
        self._lock = asyncio.Lock()  # Global fallback lock
        self._tab_locks: dict[str, asyncio.Lock] = {}  # Per-tab serialization
        self._session_file = "/data/tabs_session.json"  # Persisted tab state

    def _get_tab_lock(self, tab_id: str | None) -> asyncio.Lock:
        """Get or create a lock for a specific tab."""
        key = tab_id or "_default"
        if key not in self._tab_locks:
            self._tab_locks[key] = asyncio.Lock()
        return self._tab_locks[key]

    # ─── Session persistence ───

    def _save_tab_state(self):
        """Persist minimal tab info to disk (fire-and-forget)."""
        import json as _json
        try:
            # Schedule tab state save — runs get_tabs and writes to file
            async def _save():
                try:
                    tabs = await self.get_tabs()
                    state = [
                        {"id": t.get("id"), "url": t.get("url"), "title": t.get("title")}
                        for t in tabs if t.get("type") == "page"
                    ]
                    with open(self._session_file, "w") as f:
                        _json.dump(state, f)
                except Exception:
                    pass
            asyncio.ensure_future(_save())
        except Exception:
            pass

    async def restore_tabs(self):
        """Restore tabs from saved session state. Call on startup."""
        import json as _json
        import os
        if not os.path.exists(self._session_file):
            return
        try:
            with open(self._session_file) as f:
                saved = _json.load(f)
            if not saved:
                return
            # Get current tabs to see what's already open
            current = await self.get_tabs()
            current_urls = {t.get("url") for t in current if t.get("type") == "page"}
            for tab in saved:
                url = tab.get("url", "")
                if url and url != "about:blank" and url not in current_urls:
                    await self.create_tab(url)
                    await asyncio.sleep(0.3)
            logger.info(f"Restored {len(saved)} tab(s) from session")
        except Exception as e:
            logger.debug(f"Tab restore failed: {e}")

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
                           tab_id: str = None, timeout: float = 30.0) -> dict:
        ws_url = await self.get_ws_url(tab_id)
        tab_key = tab_id or "_default"
        async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
            # Inject stealth JS on first connection to a page
            if not self._stealth_injected.get(tab_key) and "page" in ws_url:
                try:
                    stealth_id = self._next_id()
                    await ws.send(json.dumps({
                        "id": stealth_id,
                        "method": "Page.addScriptToEvaluateOnNewDocument",
                        "params": {"source": STEALTH_JS},
                    }))
                    await asyncio.wait_for(ws.recv(), timeout=timeout)
                    self._stealth_injected[tab_key] = True
                    logger.info("Stealth JS injected via CDP")
                except Exception as e:
                    logger.warning(f"Stealth injection via CDP failed: {e}")

            msg_id = self._next_id()
            payload = {"id": msg_id, "method": method}
            if params:
                payload["params"] = params
            await ws.send(json.dumps(payload))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                raise TimeoutError(f"CDP command '{method}' timed out after {timeout}s")
            resp = json.loads(raw)
            return resp.get("result", resp)

    async def _send_on_ws(self, ws, method: str, params: dict = None,
                          timeout: float = 30.0) -> dict:
        """Send a CDP command on an existing WebSocket connection.

        Loops recv until it gets the response matching its msg_id.
        Buffers any events received in the meantime onto ws._event_buffer.
        """
        msg_id = self._next_id()
        payload = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        await ws.send(json.dumps(payload))
        # Ensure event buffer exists on the ws object
        if not hasattr(ws, "_event_buffer"):
            ws._event_buffer = []
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                msg = json.loads(raw)
                if msg.get("id") == msg_id:
                    return msg.get("result", msg)
                # Buffer events for later consumption
                if "method" in msg:
                    ws._event_buffer.append(msg)
                # Discard responses to other commands we don't care about
        except asyncio.TimeoutError:
            raise TimeoutError(f"CDP command '{method}' timed out after {timeout}s")

    async def _get_ws(self, tab_id: str = None):
        """Get an open WebSocket connection to the active page tab."""
        ws_url = await self.get_ws_url(tab_id)
        tab_key = tab_id or "_default"
        ws = await websockets.connect(ws_url, max_size=10 * 1024 * 1024).__aenter__()
        if not self._stealth_injected.get(tab_key):
            try:
                await self._send_on_ws(ws, "Page.addScriptToEvaluateOnNewDocument",
                                       {"source": STEALTH_JS})
                self._stealth_injected[tab_key] = True
            except Exception:
                pass
        return ws

    async def navigate(self, url: str, tab_id: str = None,
                       wait_for: str = None, wait_strategy: str = "none",
                       timeout: float = 30.0, extract: bool = False,
                       dismiss_obstacles: bool = False,
                       referer: Optional[str] = None,
                       viewport: Optional[dict] = None,
                       human_pause: bool = True) -> dict:
        """Navigate Chrome to a URL with optional smart wait, extraction, and obstacle handling.

        Args:
            url: URL to navigate to.
            tab_id: Target tab ID (None = first page tab).
            wait_for: CSS selector to wait for (used with wait_strategy="selector").
            wait_strategy: "none" (fire-and-forget), "dom", "network_idle", "smart", "selector".
            timeout: Max time in seconds for the entire operation.
            extract: Run structured extraction and include in result.
            dismiss_obstacles: Auto-dismiss cookie banners and popups.
            referer: Referer header to send.
            viewport: {"width": int, "height": int} for viewport override.
            human_pause: Add human-like pause and anti-detection.

        Returns:
            dict with frameId, loaderId, url, and optional wait/obstacles/extraction keys.
        """
        if _recorder and _recorder.recording:
            _recorder.record_step("navigate", {"url": url})

        tab_key = tab_id or "_default"
        nav_result = {}
        lock = self._get_tab_lock(tab_id)
        ws = None

        # Phase 1: Send Page.navigate (hold lock)
        async with lock:
            self._stealth_injected[tab_key] = False
            ws = await self._get_ws(tab_id)
            try:
                # Set referer if provided
                if referer:
                    try:
                        await self._send_on_ws(ws, "Network.setExtraHTTPHeaders",
                                               {"headers": {"Referer": referer}})
                    except Exception:
                        pass

                # Set viewport if provided
                if viewport:
                    try:
                        await self._send_on_ws(ws, "Emulation.setDeviceMetricsOverride", {
                            "width": viewport.get("width", 1920),
                            "height": viewport.get("height", 1080),
                            "deviceScaleFactor": 1,
                            "mobile": False,
                        })
                    except Exception:
                        pass

                # Anti-detection: viewport jitter when no explicit viewport
                if not viewport and human_pause:
                    try:
                        base_w, base_h = 1920, 1080
                        jw = base_w + random.randint(-50, 50)
                        jh = base_h + random.randint(-50, 50)
                        await self._send_on_ws(ws, "Emulation.setDeviceMetricsOverride", {
                            "width": jw, "height": jh,
                            "deviceScaleFactor": 1, "mobile": False,
                        })
                    except Exception:
                        pass

                # Anti-detection: referer spoofing when no explicit referer
                if not referer and human_pause:
                    try:
                        parsed = urlparse(url)
                        domain = parsed.hostname or ""
                        fake_ref = f"https://www.google.com/search?q={domain}"
                        await self._send_on_ws(ws, "Network.setExtraHTTPHeaders",
                                               {"headers": {"Referer": fake_ref}})
                    except Exception:
                        pass

                # Enable Page events BEFORE navigate so we receive load events
                if wait_strategy != "none":
                    try:
                        await self._send_on_ws(ws, "Page.enable")
                    except Exception:
                        pass

                # Send Page.navigate
                result = await self._send_on_ws(ws, "Page.navigate", {"url": url})
                if result.get("errorText"):
                    raise NavigationError(result["errorText"])

                frame_id = result.get("frameId", "")
                loader_id = result.get("loaderId", "")
                nav_result = {"frameId": frame_id, "loaderId": loader_id, "url": url}
            except NavigationError:
                raise
            except Exception:
                raise

        # Phase 2: Wait for load — reuse same WS (event buffer preserved)
        try:
            if wait_strategy != "none":
                wait_result = await self._wait_for_load(
                    ws, tab_id, wait_for, wait_strategy, timeout)
                nav_result["wait"] = wait_result

            # Phase 3: Obstacle detection + extraction — reuse same WS
            if dismiss_obstacles or extract:
                if dismiss_obstacles:
                    obstacles = await self._detect_and_dismiss_obstacles(ws, tab_id)
                    nav_result["obstacles"] = obstacles

                if extract:
                    extraction = await self._extract_structured(ws, tab_id)
                    nav_result["extraction"] = extraction
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WS closed during wait/extract phase")
        except Exception as e:
            logger.warning(f"Post-navigation phase failed: {e}")
        finally:
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass

        # Human pause
        if human_pause:
            await asyncio.sleep(random.uniform(0.2, 0.8))

        from core.events import emit
        emit("page.loaded", {"url": url})
        return nav_result

    def _wait_result(self, strategy, start, load_event, dom_event, network_idle, nav_error, selector_found=False):
        """Build the wait result dict."""
        return {
            "strategy": strategy,
            "waited_ms": int((time.monotonic() - start) * 1000),
            "load_event": load_event,
            "dom_event": dom_event,
            "network_idle": network_idle,
            "selector_found": selector_found,
            **({"nav_error": nav_error} if nav_error else {}),
        }

    async def _wait_for_load(self, ws, tab_id: str, wait_for: Optional[str],
                              strategy: str, timeout: float) -> dict:
        """Wait for page load using persistent WebSocket and raw recv loop.

        Args:
            ws: Open WebSocket connection.
            tab_id: Tab identifier.
            wait_for: CSS selector (for selector strategy).
            strategy: "dom", "network_idle", "smart", or "selector".
            timeout: Max time in seconds for the wait phase.

        Returns:
            dict with strategy, waited_ms, load_event, network_idle, selector_found.
        """
        start = time.monotonic()
        deadline = time.monotonic() + timeout
        load_event_fired = False
        dom_event_fired = False
        network_idle = False
        last_network_activity = start
        nav_error = None

        # Network.enable for network_idle tracking (Page.enable already sent in navigate)
        try:
            await self._send_on_ws(ws, "Network.enable")
        except Exception as e:
            return {"strategy": strategy, "waited_ms": 0, "error": f"Network.enable failed: {e}"}

        # Helper to process a single event message
        def _process_event(msg):
            nonlocal load_event_fired, dom_event_fired, network_idle, last_network_activity, nav_error
            method = msg.get("method", "")
            if method == "Page.domContentEventFired":
                dom_event_fired = True
                return False  # don't auto-break; let caller decide
            elif method == "Page.loadEventFired":
                load_event_fired = True
            elif method.startswith("Network.") and not method.startswith("Network.requestWillBeSent"):
                last_network_activity = time.monotonic()
                if method == "Network.loadingFailed":
                    params = msg.get("params", {})
                    if not params.get("encodedDataLength"):
                        nav_error = "loading_failed"
                if strategy in ("network_idle", "smart"):
                    if time.monotonic() - last_network_activity > 0.5:
                        network_idle = True
                        if strategy == "network_idle":
                            return True
                        if strategy == "smart" and (load_event_fired or dom_event_fired):
                            return True
            return False

        # Drain event buffer from prior _send_on_ws calls
        if hasattr(ws, "_event_buffer"):
            for msg in ws._event_buffer:
                if _process_event(msg):
                    if strategy == "dom" and dom_event_fired:
                        return self._wait_result(strategy, start, load_event_fired, dom_event_fired, network_idle, nav_error)
                    if strategy == "smart" and load_event_fired and network_idle:
                        return self._wait_result(strategy, start, load_event_fired, dom_event_fired, network_idle, nav_error)
            ws._event_buffer.clear()

        try:
            # Check if page is already loaded (handles same-URL/cached navigations
            # where Chrome doesn't emit domContentEventFired)
            if strategy in ("dom", "selector", "smart"):
                try:
                    ready = await self._send_on_ws(ws, "Runtime.evaluate", {
                        "expression": "document.readyState",
                        "returnByValue": True,
                    })
                    ready_state = ready.get("result", {}).get("value", "loading")
                    if ready_state in ("interactive", "complete"):
                        dom_event_fired = True
                        if strategy == "dom":
                            return self._wait_result(strategy, start, load_event_fired, dom_event_fired, network_idle, nav_error)
                        if strategy == "selector" and dom_event_fired:
                            # Fall through to selector poll
                            pass
                except Exception:
                    pass

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 1.0))
                except asyncio.TimeoutError:
                    # Check network idle on timeout (500ms since last Network.* event)
                    if strategy in ("network_idle", "smart"):
                        if time.monotonic() - last_network_activity > 0.5:
                            network_idle = True
                            if strategy == "network_idle" or (strategy == "smart" and (load_event_fired or dom_event_fired)):
                                break
                    # Selector: break if DOM already loaded (no more events coming)
                    if strategy == "selector" and dom_event_fired:
                        break
                    continue

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # Command responses (have "id") — discard
                if "id" in msg:
                    continue

                # Process events
                if "method" in msg:
                    should_break = _process_event(msg)
                    if should_break:
                        break
                    # Check dom/selector break conditions after event processing
                    if strategy == "dom" and dom_event_fired:
                        break
                    if strategy == "selector" and dom_event_fired:
                        break
        except websockets.exceptions.ConnectionClosed:
            pass

        waited_ms = int((time.monotonic() - start) * 1000)

        # Clear any remaining buffered events before poll/extraction phase
        if hasattr(ws, "_event_buffer"):
            ws._event_buffer.clear()

        # Disable page/network events to stop the event stream before poll/extraction
        try:
            await self._send_on_ws(ws, "Page.disable")
            await self._send_on_ws(ws, "Network.disable")
        except Exception:
            pass

        # Selector poll if needed (short per-iteration timeout to avoid hanging on buffered events)
        selector_found = False
        if wait_for and (strategy == "selector" or strategy == "smart"):
            try:
                remaining = max(0, deadline - time.monotonic())
                poll_timeout = min(3.0, remaining)
                for i in range(int(remaining / 0.3)):
                    if time.monotonic() >= deadline:
                        break
                    try:
                        result = await self._send_on_ws(ws, "Runtime.evaluate", {
                            "expression": f'document.querySelector("{wait_for}") !== null',
                            "returnByValue": True,
                        }, timeout=poll_timeout)
                        val = result.get("result", {}).get("value")
                        if val is True:
                            selector_found = True
                            break
                    except (TimeoutError, asyncio.TimeoutError):
                        break
                    await asyncio.sleep(0.3)
            except Exception:
                pass

        # Clear any events buffered during selector poll
        if hasattr(ws, "_event_buffer"):
            ws._event_buffer.clear()

        # Events already disabled above before poll phase

        return self._wait_result(strategy, start, load_event_fired, dom_event_fired, network_idle, nav_error, selector_found)

    async def _detect_and_dismiss_obstacles(self, ws, tab_id: str) -> dict:
        """Detect and auto-dismiss cookie banners and popups.

        Returns dict of detected obstacles.
        """
        try:
            result = await self._send_on_ws(ws, "Runtime.evaluate", {
                "expression": OBSTACLE_DETECT_JS,
                "returnByValue": True,
            })
            obstacles = result.get("result", {}).get("value", {})
            if not isinstance(obstacles, dict):
                obstacles = {}
        except Exception:
            obstacles = {}

        # Dismiss cookie banners
        if "cookie_banner" in obstacles:
            try:
                await self._send_on_ws(ws, "Runtime.evaluate", {
                    "expression": OBSTACLE_DISMISS_COOKIE_JS,
                    "returnByValue": True,
                })
                await asyncio.sleep(0.3)
            except Exception:
                pass

        # Dismiss popups
        if "popup" in obstacles:
            try:
                await self._send_on_ws(ws, "Runtime.evaluate", {
                    "expression": OBSTACLE_DISMISS_POPUP_JS,
                    "returnByValue": True,
                })
                await asyncio.sleep(0.3)
            except Exception:
                pass

        return obstacles

    async def _extract_structured(self, ws, tab_id: str) -> dict:
        """Run structured extraction JS and return parsed result.

        Network.disable must have been called before this to avoid
        multiplexing issues on the WS recv loop.
        """
        try:
            result = await self._send_on_ws(ws, "Runtime.evaluate", {
                "expression": EXTRACT_STRUCTURED_JS,
                "returnByValue": True,
                "awaitPromise": True,
            })
            value = result.get("result", {}).get("value")
            if isinstance(value, dict):
                return value
            return {}
        except Exception as e:
            logger.warning(f"Structured extraction failed: {e}")
            return {}

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
        async with self._get_tab_lock(tab_id):
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
        async with self._get_tab_lock(tab_id):
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
        """Type text into an element. For contenteditable/ProseMirror/rich-textarea,
        uses JS injection to avoid doubled characters from keyDown+char events."""
        if _recorder and _recorder.recording:
            _recorder.record_step("type", {"selector": selector, "text": text})
        async with self._get_tab_lock(tab_id):
            node_id = await self.query_selector(selector, tab_id=tab_id)

            # Check if element needs JS injection (contenteditable, ProseMirror, or rich-textarea)
            needs_js = await self.evaluate(
                f'(function(){{'
                f'const el = document.querySelector("{selector}");'
                f'if(!el) return false;'
                f'if(el.contentEditable === "true") return true;'
                f'if(el.tagName?.includes("-")) return true;'  # custom element (rich-textarea etc.)
                f'const inner = el.querySelector("[contenteditable=true], .ql-editor, .ProseMirror");'
                f'return !!inner;'
                f'}})()',
                tab_id=tab_id,
            )

            if needs_js:
                # Use JS injection for contenteditable/rich editors — avoids doubled chars
                escaped = text.replace("\\", "\\\\").replace('"', '\\"')
                await self.evaluate(
                    f'(function(){{ '
                    f'const el = document.querySelector("{selector}"); '
                    f'if(!el) return "not found"; '
                    f'const target = el.querySelector("[contenteditable=true], .ql-editor, .ProseMirror") || el; '
                    f'target.focus(); '
                    f'document.execCommand("insertText", false, "{escaped}"); '
                    f'target.dispatchEvent(new Event("input", {{bubbles: true}})); '
                    f'return "done"; '
                    f'}})()',
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
        async with self._get_tab_lock(tab_id):
            await self.send_command("Input.dispatchMouseEvent", {
                "type": "mouseWheel",
                "x": 0, "y": 0,
                "deltaX": x, "deltaY": y,
            }, tab_id=tab_id)
        return {"status": "scrolled", "x": x, "y": y}

    # ─── Tab management ───

    async def create_tab(self, url: str = "about:blank") -> dict:
        """Open a new Chrome tab and optionally navigate to URL."""
        async with self._lock:
            result = await self.send_command("Target.createTarget", {"url": url})
        target_id = result.get("targetId")
        self._save_tab_state()
        return {"status": "created", "targetId": target_id, "url": url}

    async def close_tab(self, target_id: str) -> dict:
        """Close a Chrome tab by target ID."""
        async with self._lock:
            await self.send_command("Target.closeTarget", {"targetId": target_id})
        self._save_tab_state()
        return {"status": "closed", "targetId": target_id}

    async def activate_tab(self, target_id: str) -> dict:
        """Activate (focus) a Chrome tab by target ID."""
        async with self._lock:
            await self.send_command("Target.activateTarget", {"targetId": target_id})
        return {"status": "activated", "targetId": target_id}

    # ─── Wait / PDF ───

    async def wait_for_selector(self, selector: str, timeout: int = 10000, tab_id: str = None) -> dict:
        """Poll until an element matching the selector appears. Returns when found or timeout.
        Uses both DOM.querySelector and JS querySelector for shadow DOM support."""
        interval = 0.3
        elapsed = 0.0
        while elapsed < timeout / 1000.0:
            async with self._get_tab_lock(tab_id):
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
        async with self._get_tab_lock(tab_id):
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

    # ─── Fast / Raw CDP methods ───

    async def send_raw(self, method: str, params: dict = None, tab_id: str = None) -> dict:
        """Send any raw CDP protocol command. No lock — caller controls concurrency."""
        return await self.send_command(method, params, tab_id=tab_id)

    async def insert_text(self, text: str, tab_id: str = None) -> dict:
        """Insert text at cursor using CDP Input.insertText.
        Triggers all browser events natively — works with React/Vue.
        Much faster than character-by-character type_text."""
        if _recorder and _recorder.recording:
            _recorder.record_step("insert_text", {"text": text})
        async with self._get_tab_lock(tab_id):
            result = await self.send_command("Input.insertText", {"text": text}, tab_id=tab_id)
        return {"status": "inserted", "text": text}

    async def click_at(self, x: float, y: float, tab_id: str = None) -> dict:
        """Fast CDP mouse click at exact coordinates. No human-like delays."""
        if _recorder and _recorder.recording:
            _recorder.record_step("click_at", {"x": x, "y": y})
        async with self._get_tab_lock(tab_id):
            await self.send_command("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": x, "y": y,
                "button": "left", "clickCount": 1,
            }, tab_id=tab_id)
            await self.send_command("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": x, "y": y,
                "button": "left", "clickCount": 1,
            }, tab_id=tab_id)
        return {"status": "clicked", "x": x, "y": y}

    async def press_key(self, key: str, tab_id: str = None) -> dict:
        """Press a key or key combo (e.g. "ctrl+a", "Enter", "Escape").
        Dispatches CDP Input.dispatchKeyEvent for each key event."""
        if _recorder and _recorder.recording:
            _recorder.record_step("press_key", {"key": key})

        # Parse combo syntax: "ctrl+a" → modifier + key
        parts = key.lower().replace(" ", "").split("+")
        modifiers = 0
        actual_key = parts[-1]

        for part in parts[:-1]:
            if part in _MODIFIER_MAP:
                modifiers |= _MODIFIER_MAP[part]

        # Resolve the actual key
        resolved = _KEY_MAP.get(actual_key)
        if not resolved:
            # Single character (a-z, 0-9, symbols)
            code = actual_key.upper()
            vk = ord(actual_key.upper()) if len(actual_key) == 1 else 0
            resolved = {"key": actual_key, "code": f"Key{code}" if actual_key.isalpha() else code, "windowsVirtualKeyCode": vk}

        async with self._get_tab_lock(tab_id):
            # Press modifier keys
            mod_names = [p for p in parts[:-1] if p in _MODIFIER_MAP]
            for mod in mod_names:
                mod_bit = _MODIFIER_MAP[mod]
                mod_key = {"ctrl": "Control", "control": "Control", "shift": "Shift",
                           "alt": "Alt", "meta": "Meta", "cmd": "Meta", "command": "Meta"}[mod]
                await self.send_command("Input.dispatchKeyEvent", {
                    "type": "keyDown",
                    "key": mod_key,
                    "code": f"{mod_key}Left",
                    "windowsVirtualKeyCode": 17 if mod_bit == 2 else (16 if mod_bit == 8 else (18 if mod_bit == 1 else 91)),
                    "modifiers": modifiers,
                }, tab_id=tab_id)

            # Press the actual key
            key_down_params = {
                "type": "keyDown",
                "key": resolved["key"],
                "code": resolved["code"],
                "windowsVirtualKeyCode": resolved["windowsVirtualKeyCode"],
            }
            if modifiers:
                key_down_params["modifiers"] = modifiers
            # Add text for printable keys
            if len(actual_key) == 1 and actual_key.isprintable():
                key_down_params["text"] = actual_key
            await self.send_command("Input.dispatchKeyEvent", key_down_params, tab_id=tab_id)

            key_up_params = {
                "type": "keyUp",
                "key": resolved["key"],
                "code": resolved["code"],
                "windowsVirtualKeyCode": resolved["windowsVirtualKeyCode"],
            }
            if modifiers:
                key_up_params["modifiers"] = modifiers
            await self.send_command("Input.dispatchKeyEvent", key_up_params, tab_id=tab_id)

            # Release modifier keys in reverse order
            for mod in reversed(mod_names):
                mod_bit = _MODIFIER_MAP[mod]
                mod_key = {"ctrl": "Control", "control": "Control", "shift": "Shift",
                           "alt": "Alt", "meta": "Meta", "cmd": "Meta", "command": "Meta"}[mod]
                await self.send_command("Input.dispatchKeyEvent", {
                    "type": "keyUp",
                    "key": mod_key,
                    "code": f"{mod_key}Left",
                    "windowsVirtualKeyCode": 17 if mod_bit == 2 else (16 if mod_bit == 8 else (18 if mod_bit == 1 else 91)),
                }, tab_id=tab_id)

        return {"status": "pressed", "key": key}

    async def click_iframe(self, selector: str, iframe_selector: str = "iframe",
                           tab_id: str = None) -> dict:
        """Click an element inside an iframe. Gets iframe's document,
        queries element within it, and clicks at its coordinates."""
        async with self._get_tab_lock(tab_id):
            # Get iframe content document and click coordinates
            js = (
                f'(function(){{'
                f'const iframe = document.querySelector("{iframe_selector}");'
                f'if(!iframe || !iframe.contentDocument) return {{error: "iframe not found"}};'
                f'const el = iframe.contentDocument.querySelector("{selector}");'
                f'if(!el) return {{error: "element not found in iframe"}};'
                f'const rect = el.getBoundingClientRect();'
                f'const iframeRect = iframe.getBoundingClientRect();'
                f'return {{'
                f'x: iframeRect.left + rect.left + rect.width/2,'
                f'y: iframeRect.top + rect.top + rect.height/2,'
                f'width: rect.width, height: rect.height'
                f'}};'
                f'}})()'
            )
            result = await self.send_command(
                "Runtime.evaluate",
                {"expression": js, "returnByValue": True},
                tab_id=tab_id,
            )
            pos = result.get("result", {}).get("value", {})
            if "error" in pos:
                return pos
            x, y = pos["x"], pos["y"]
            await self.send_command("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": x, "y": y,
                "button": "left", "clickCount": 1,
            }, tab_id=tab_id)
            await self.send_command("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": x, "y": y,
                "button": "left", "clickCount": 1,
            }, tab_id=tab_id)
        return {"status": "clicked_iframe", "selector": selector, "x": round(x, 1), "y": round(y, 1)}

    async def type_iframe(self, selector: str, text: str, iframe_selector: str = "iframe",
                          tab_id: str = None) -> dict:
        """Click into an element inside an iframe, then insert text natively."""
        async with self._get_tab_lock(tab_id):
            # Focus the element in the iframe
            js = (
                f'(function(){{'
                f'const iframe = document.querySelector("{iframe_selector}");'
                f'if(!iframe || !iframe.contentDocument) return "iframe not found";'
                f'const el = iframe.contentDocument.querySelector("{selector}");'
                f'if(!el) return "element not found";'
                f'el.focus();'
                f'el.click();'
                f'return "focused";'
                f'}})()'
            )
            result = await self.send_command(
                "Runtime.evaluate",
                {"expression": js, "returnByValue": True},
                tab_id=tab_id,
            )
            status = result.get("result", {}).get("value", "")
            if status != "focused":
                return {"error": status}
            # Insert text natively via CDP
            await self.send_command("Input.insertText", {"text": text}, tab_id=tab_id)
        return {"status": "typed_iframe", "selector": selector, "text": text}

    async def upload_file_url(self, selector: str, file_url: str, tab_id: str = None) -> dict:
        """Download a file from URL and set it as the value of a file input.
        Bypasses the need for base64 encoding."""
        import httpx
        import tempfile
        import os
        async with self._get_tab_lock(tab_id):
            # Download the file
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.get(file_url, follow_redirects=True)
                r.raise_for_status()
                file_data = r.content
                content_type = r.headers.get("content-type", "")
            # Save to temp file (CDP needs a real file path)
            ext = ".jpg"
            if "png" in content_type:
                ext = ".png"
            elif "gif" in content_type:
                ext = ".gif"
            elif "webp" in content_type:
                ext = ".webp"
            tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
            tmp.write(file_data)
            tmp.close()

            # Find the file input and set the file
            node_id = await self.query_selector(selector, tab_id=tab_id)
            if not node_id:
                os.unlink(tmp.name)
                return {"error": "File input not found"}
            await self.send_command("DOM.focus", {"nodeId": node_id}, tab_id=tab_id)
            # Set file on the input using DOM.setFileInputFiles
            result = await self.send_command("DOM.setFileInputFiles", {
                "files": [tmp.name],
                "nodeId": node_id,
            }, tab_id=tab_id)
            # Cleanup temp file
            os.unlink(tmp.name)
        return {"status": "uploaded", "url": file_url, "size": len(file_data)}

    async def upload_local_file(self, selector: str, file_path: str, tab_id: str = None) -> dict:
        """Upload a local file (already on disk) to a file input via DOM.setFileInputFiles."""
        import os
        async with self._get_tab_lock(tab_id):
            node_id = await self.query_selector(selector, tab_id=tab_id)
            if not node_id:
                return {"error": "File input not found"}
            await self.send_command("DOM.focus", {"nodeId": node_id}, tab_id=tab_id)
            await self.send_command("DOM.setFileInputFiles", {
                "files": [file_path],
                "nodeId": node_id,
            }, tab_id=tab_id)
        size = os.path.getsize(file_path)
        return {"status": "uploaded", "path": file_path, "size": size}


# Global instance
cdp = CDPBridge()
