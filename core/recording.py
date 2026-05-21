"""Screen recording engine via CDP Page.startScreencast.

Uses a persistent WebSocket to capture JPEG frames, then compiles into
video (WebM/MP4) via opencv-python-headless. Falls back to frame list
if opencv is unavailable.
"""

import asyncio
import base64
import io
import json
import logging
import time
from typing import Optional

import websockets

from config import settings

logger = logging.getLogger(__name__)


class ScreenRecorder:
    """Singleton screen recorder using CDP screencast."""

    def __init__(self):
        self._recording = False
        self._frames: list[bytes] = []
        self._ws = None
        self._task: Optional[asyncio.Task] = None
        self._fps: int = 10
        self._quality: int = 80
        self._start_time: float = 0
        self._frame_count: int = 0

    @property
    def recording(self) -> bool:
        return self._recording

    async def start(self, fps: int = 10, quality: int = 80) -> dict:
        """Start screen recording via CDP screencast."""
        if self._recording:
            return {"error": "Already recording"}

        self._fps = max(1, min(fps, 30))
        self._quality = max(10, min(quality, 100))
        self._frames = []
        self._frame_count = 0
        self._start_time = time.monotonic()

        # Get WebSocket URL
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
            return {"error": "No Chrome tab available"}

        self._recording = True
        self._task = asyncio.create_task(self._screencast_loop(ws_url))
        logger.info(f"Screen recording started ({self._fps}fps, quality={self._quality})")
        return {"status": "recording", "fps": self._fps, "quality": self._quality}

    async def _screencast_loop(self, ws_url: str):
        """Persistent WebSocket loop that captures screencast frames."""
        try:
            async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
                self._ws = ws

                # Start screencast
                await ws.send(json.dumps({
                    "id": 1,
                    "method": "Page.startScreencast",
                    "params": {
                        "format": "jpeg",
                        "quality": self._quality,
                        "maxWidth": 1920,
                        "maxHeight": 1080,
                        "everyNthFrame": max(1, 30 // self._fps),
                    },
                }))

                msg_id = 2
                while self._recording:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        msg = json.loads(raw)

                        if msg.get("method") == "Page.screencastFrame":
                            frame_data = base64.b64decode(msg["params"]["data"])
                            self._frames.append(frame_data)
                            self._frame_count += 1
                            session_id = msg["params"]["sessionId"]

                            # Acknowledge frame
                            await ws.send(json.dumps({
                                "id": msg_id,
                                "method": "Page.screencastFrameAck",
                                "params": {"sessionId": session_id},
                            }))
                            msg_id += 1

                    except asyncio.TimeoutError:
                        continue
                    except websockets.ConnectionClosed:
                        break

                # Stop screencast
                try:
                    await ws.send(json.dumps({
                        "id": msg_id,
                        "method": "Page.stopScreencast",
                        "params": {},
                    }))
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Screencast loop error: {e}")
        finally:
            self._ws = None

    async def stop(self) -> dict:
        """Stop recording and compile video."""
        if not self._recording:
            return {"error": "Not recording"}

        self._recording = False

        # Wait for the screencast task to finish
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()

        duration_ms = int((time.monotonic() - self._start_time) * 1000)
        frame_count = len(self._frames)

        # Emit event
        from core.events import emit
        emit("recording.done", {"frames": frame_count, "duration_ms": duration_ms})

        if frame_count == 0:
            return {
                "status": "stopped",
                "frames": 0,
                "duration_ms": duration_ms,
                "error": "No frames captured",
            }

        # Try to compile video with opencv
        try:
            import cv2
            import numpy as np

            # Get dimensions from first frame
            first_frame = cv2.imdecode(np.frombuffer(self._frames[0], np.uint8), cv2.IMREAD_COLOR)
            if first_frame is None:
                raise ValueError("Could not decode first frame")
            h, w = first_frame.shape[:2]

            # Encode as WebM (VP80) to memory
            output_buf = io.BytesIO()
            fourcc = cv2.VideoWriter_fourcc(*'VP80')
            writer = cv2.VideoWriter()
            # Use raw file path workaround — write temp, read back
            import tempfile, os
            tmp_fd, tmp_path = tempfile.mkstemp(suffix='.webm')
            os.close(tmp_fd)

            writer.open(tmp_path, fourcc, self._fps, (w, h))
            if not writer.isOpened():
                # Fallback: try mp4v
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                os.unlink(tmp_path)
                tmp_fd, tmp_path = tempfile.mkstemp(suffix='.mp4')
                os.close(tmp_fd)
                writer.open(tmp_path, fourcc, self._fps, (w, h))

            if writer.isOpened():
                for frame_bytes in self._frames:
                    img = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR)
                    if img is not None:
                        writer.write(img)
                writer.release()

                with open(tmp_path, 'rb') as f:
                    video_data = f.read()
                os.unlink(tmp_path)

                video_b64 = base64.b64encode(video_data).decode()
                logger.info(f"Screen recording compiled: {frame_count} frames, {len(video_data)} bytes")
                return {
                    "status": "stopped",
                    "frames": frame_count,
                    "duration_ms": duration_ms,
                    "video_base64": video_b64,
                    "video_size": len(video_data),
                    "format": "video/webm",
                }
            else:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise ValueError("VideoWriter failed to open")

        except ImportError:
            logger.info("opencv not available, returning frames as base64 images")
        except Exception as e:
            logger.warning(f"Video compilation failed ({e}), returning frames")

        # Fallback: return frames as base64 image list
        frames_b64 = [base64.b64encode(f).decode() for f in self._frames[:100]]  # Cap at 100 frames
        return {
            "status": "stopped",
            "frames": frame_count,
            "frames_returned": len(frames_b64),
            "duration_ms": duration_ms,
            "frame_images_base64": frames_b64,
            "format": "image/jpeg",
        }

    def status(self) -> dict:
        """Current recording state."""
        if not self._recording:
            return {"status": "idle"}
        elapsed = int((time.monotonic() - self._start_time) * 1000)
        return {
            "status": "recording",
            "fps": self._fps,
            "quality": self._quality,
            "frames": self._frame_count,
            "elapsed_ms": elapsed,
        }


screen_recorder = ScreenRecorder()
