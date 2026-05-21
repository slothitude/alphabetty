"""Live viewport streaming via CDP Page.startScreencast.

Yields JPEG frames as an async generator for MJPEG streaming.
Checks screen_recorder to avoid conflict with video recording.
"""

import asyncio
import base64
import json
import logging
from typing import AsyncGenerator

import websockets

from config import settings

logger = logging.getLogger(__name__)


async def mjpeg_frames(fps: int = 8, quality: int = 60) -> AsyncGenerator[bytes, None]:
    """Yield raw JPEG frames from Chrome's viewport via CDP screencast.

    This is a long-lived async generator — the caller drives iteration.
    Stops when the caller stops consuming (client disconnect).
    """
    from core.recording import screen_recorder

    if screen_recorder.recording:
        raise RuntimeError("Screen recorder is active — cannot start live stream")

    # Discover Chrome tab WS URL
    import httpx
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{settings.cdp_url}/json/list")
        resp.raise_for_status()
        tabs = resp.json()
        ws_url = None
        for tab in tabs:
            if tab.get("type") == "page":
                ws_url = tab["webSocketDebuggerUrl"]
                break
        if not ws_url and tabs:
            ws_url = tabs[0]["webSocketDebuggerUrl"]

    if not ws_url:
        raise RuntimeError("No Chrome tab available")

    every_nth = max(1, 30 // max(fps, 1))

    try:
        async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
            # Start screencast
            await ws.send(json.dumps({
                "id": 1,
                "method": "Page.startScreencast",
                "params": {
                    "format": "jpeg",
                    "quality": quality,
                    "maxWidth": 1280,
                    "maxHeight": 720,
                    "everyNthFrame": every_nth,
                },
            }))

            msg_id = 2
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    msg = json.loads(raw)

                    if msg.get("method") == "Page.screencastFrame":
                        frame_data = base64.b64decode(msg["params"]["data"])
                        session_id = msg["params"]["sessionId"]

                        # Acknowledge immediately
                        await ws.send(json.dumps({
                            "id": msg_id,
                            "method": "Page.screencastFrameAck",
                            "params": {"sessionId": session_id},
                        }))
                        msg_id += 1

                        yield frame_data

                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed:
                    break
    except Exception as e:
        logger.error(f"Live stream error: {e}")
        raise
