"""Macro recording/playback engine.

Supports two recording modes:
  API-level: intercepts CDP bridge calls (navigate, click, type, scroll, evaluate)
  Browser-level: injects JS event listeners, polls captured events on stop

Playback replays steps via CDP bridge with timing (capped 2s delays).
"""

import asyncio
import json
import logging
import time
from typing import Optional

from core.cdp_bridge import cdp

logger = logging.getLogger(__name__)


class MacroRecorder:
    """Singleton macro recorder / playback engine."""

    def __init__(self):
        self._recording = False
        self._browser_recording = False
        self._steps: list[dict] = []
        self._start_time: float = 0
        self._macro_name: str = ""
        self._macro_url: str = ""

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def browser_recording(self) -> bool:
        return self._browser_recording

    def start(self, name: str, url: str = "") -> dict:
        """Start API-level recording."""
        if self._recording or self._browser_recording:
            return {"error": "Already recording"}
        self._recording = True
        self._steps = []
        self._start_time = time.monotonic()
        self._macro_name = name
        self._macro_url = url
        logger.info(f"Macro recording started: {name}")
        return {"status": "recording", "name": name}

    def record_step(self, action: str, params: dict):
        """Capture a single step (called from cdp_bridge hooks)."""
        if not self._recording:
            return
        elapsed = int((time.monotonic() - self._start_time) * 1000)
        self._steps.append({
            "action": action,
            "params": params,
            "timestamp_ms": elapsed,
        })

    async def stop(self) -> dict:
        """Stop API-level recording and save macro to DB."""
        if not self._recording:
            return {"error": "Not recording"}
        self._recording = False
        duration_ms = int((time.monotonic() - self._start_time) * 1000)

        macro = await self._save_macro(self._macro_name, self._macro_url, self._steps, duration_ms)
        logger.info(f"Macro recording stopped: {self._macro_name} ({len(self._steps)} steps, {duration_ms}ms)")
        self._steps = []
        return macro

    async def start_browser(self, name: str, url: str = "") -> dict:
        """Start browser-level recording by injecting JS event listeners."""
        if self._recording or self._browser_recording:
            return {"error": "Already recording"}

        # Inject event capture script
        capture_js = """
        (function() {
            window.__alphabetty_events = [];
            window.__alphabetty_record_start = Date.now();

            // Click events
            document.addEventListener('click', function(e) {
                const el = e.target;
                const selector = __alphabetty_getSelector(el);
                window.__alphabetty_events.push({
                    action: 'click',
                    params: {selector: selector},
                    timestamp_ms: Date.now() - window.__alphabetty_record_start
                });
            }, true);

            // Input events (typing)
            document.addEventListener('input', function(e) {
                const el = e.target;
                if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable) {
                    const selector = __alphabetty_getSelector(el);
                    // Only record the latest value per input to avoid per-keystroke spam
                    const lastIdx = window.__alphabetty_events.length - 1;
                    if (lastIdx >= 0 && window.__alphabetty_events[lastIdx].action === 'type'
                        && window.__alphabetty_events[lastIdx].params.selector === selector) {
                        window.__alphabetty_events[lastIdx].params.text = el.value;
                        window.__alphabetty_events[lastIdx].timestamp_ms = Date.now() - window.__alphabetty_record_start;
                    } else {
                        window.__alphabetty_events.push({
                            action: 'type',
                            params: {selector: selector, text: el.value},
                            timestamp_ms: Date.now() - window.__alphabetty_record_start
                        });
                    }
                }
            }, true);

            // Scroll events (debounced)
            let scrollTimer = null;
            window.addEventListener('scroll', function() {
                if (scrollTimer) clearTimeout(scrollTimer);
                scrollTimer = setTimeout(function() {
                    window.__alphabetty_events.push({
                        action: 'scroll',
                        params: {x: window.scrollX, y: window.scrollY},
                        timestamp_ms: Date.now() - window.__alphabetty_record_start
                    });
                }, 200);
            }, true);

            // Helper: get CSS selector for element
            function __alphabetty_getSelector(el) {
                if (el.id) return '#' + el.id;
                if (el.className && typeof el.className === 'string') {
                    const cls = el.className.trim().split(/\\s+/).join('.');
                    const tag = el.tagName.toLowerCase();
                    const selector = tag + '.' + cls;
                    if (document.querySelectorAll(selector).length === 1) return selector;
                }
                // Walk up for nth-child path
                const parts = [];
                while (el && el.nodeType === 1) {
                    let selector = el.tagName.toLowerCase();
                    if (el.id) {
                        selector = '#' + el.id;
                        parts.unshift(selector);
                        break;
                    }
                    const siblings = Array.from(el.parentNode.children);
                    if (siblings.length > 1) {
                        const idx = siblings.indexOf(el) + 1;
                        selector += ':nth-child(' + idx + ')';
                    }
                    parts.unshift(selector);
                    el = el.parentNode;
                }
                return parts.join(' > ');
            }
        })();
        """
        await cdp.evaluate(capture_js)

        self._browser_recording = True
        self._start_time = time.monotonic()
        self._macro_name = name
        self._macro_url = url
        self._steps = []
        logger.info(f"Browser macro recording started: {name}")
        return {"status": "browser_recording", "name": name}

    async def stop_browser(self) -> dict:
        """Stop browser-level recording, poll captured events, save macro."""
        if not self._browser_recording:
            return {"error": "Not browser recording"}

        # Poll captured events
        events_json = await cdp.evaluate("JSON.stringify(window.__alphabetty_events || [])")
        events = []
        if events_json:
            try:
                events = json.loads(events_json) if isinstance(events_json, str) else events_json
            except (json.JSONDecodeError, TypeError):
                events = []

        self._browser_recording = False
        duration_ms = int((time.monotonic() - self._start_time) * 1000)

        # Convert scroll events to deltas
        steps = []
        prev_scroll = {"x": 0, "y": 0}
        for evt in events:
            if evt["action"] == "scroll":
                dx = evt["params"]["x"] - prev_scroll["x"]
                dy = evt["params"]["y"] - prev_scroll["y"]
                evt["params"] = {"x": dx, "y": dy}
                prev_scroll = {"x": evt["params"]["x"] + prev_scroll["x"], "y": evt["params"]["y"] + prev_scroll["y"]}
            steps.append(evt)

        macro = await self._save_macro(self._macro_name, self._macro_url, steps, duration_ms)
        logger.info(f"Browser macro recording stopped: {self._macro_name} ({len(steps)} steps, {duration_ms}ms)")
        return macro

    async def play(self, macro_id: int) -> dict:
        """Replay a saved macro with timing (capped 2s delays)."""
        macro = await self.get_macro(macro_id)
        if not macro:
            return {"error": f"Macro {macro_id} not found"}

        steps = macro["steps"]
        if not steps:
            return {"error": "Macro has no steps"}

        # Navigate to starting URL if provided
        if macro.get("url"):
            await cdp.navigate(macro["url"])
            await asyncio.sleep(0.5)

        start = time.monotonic()
        prev_ts = 0
        played = 0

        for step in steps:
            # Wait for timing (capped at 2s)
            delay_ms = min(step.get("timestamp_ms", 0) - prev_ts, 2000)
            if delay_ms > 50:
                await asyncio.sleep(delay_ms / 1000.0)
            prev_ts = step.get("timestamp_ms", 0)

            action = step["action"]
            params = step.get("params", {})

            try:
                if action == "navigate":
                    await cdp.navigate(params["url"])
                elif action == "click":
                    await cdp.click(params["selector"])
                elif action == "type":
                    await cdp.type_text(params["selector"], params["text"])
                elif action == "scroll":
                    await cdp.scroll(params.get("x", 0), params.get("y", 300))
                elif action == "evaluate":
                    await cdp.evaluate(params["expression"])
                else:
                    logger.warning(f"Unknown macro action: {action}")
                    continue
                played += 1
            except Exception as e:
                logger.warning(f"Macro step failed ({action}): {e}")

        elapsed = int((time.monotonic() - start) * 1000)
        return {
            "status": "played",
            "macro_id": macro_id,
            "macro_name": macro["name"],
            "steps_played": played,
            "steps_total": len(steps),
            "playback_ms": elapsed,
        }

    def status(self) -> dict:
        """Current recording state."""
        if self._recording:
            return {"status": "recording", "mode": "api", "name": self._macro_name, "steps": len(self._steps)}
        if self._browser_recording:
            return {"status": "recording", "mode": "browser", "name": self._macro_name}
        return {"status": "idle"}

    # ─── CRUD ───

    async def _save_macro(self, name: str, url: str, steps: list[dict], duration_ms: int) -> dict:
        from app import async_session
        from models.macro import Macro

        async with async_session() as db:
            macro = Macro(
                name=name,
                url=url,
                steps=steps,
                step_count=len(steps),
                duration_ms=duration_ms,
            )
            db.add(macro)
            await db.commit()
            await db.refresh(macro)
            return macro.to_dict()

    @staticmethod
    async def list_macros() -> list[dict]:
        from app import async_session
        from sqlalchemy import select
        from models.macro import Macro

        async with async_session() as db:
            result = await db.execute(select(Macro).order_by(Macro.created_at.desc()))
            macros = result.scalars().all()
            return [m.to_dict() for m in macros]

    @staticmethod
    async def get_macro(macro_id: int) -> Optional[dict]:
        from app import async_session
        from sqlalchemy import select
        from models.macro import Macro

        async with async_session() as db:
            result = await db.execute(select(Macro).where(Macro.id == macro_id))
            macro = result.scalar_one_or_none()
            return macro.to_dict() if macro else None

    @staticmethod
    async def delete_macro(macro_id: int) -> dict:
        from app import async_session
        from sqlalchemy import select
        from models.macro import Macro

        async with async_session() as db:
            result = await db.execute(select(Macro).where(Macro.id == macro_id))
            macro = result.scalar_one_or_none()
            if macro:
                await db.delete(macro)
                await db.commit()
                return {"ok": True, "deleted": macro_id}
            return {"error": "Not found"}


recorder = MacroRecorder()
