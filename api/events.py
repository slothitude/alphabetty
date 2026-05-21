"""SSE event stream endpoint — external agents and frontend can subscribe to Alphabetty events."""

import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from core.events import subscribe

router = APIRouter(tags=["events"])
logger = logging.getLogger(__name__)


@router.get("/events")
async def event_stream():
    """SSE stream of all events. Connect via EventSource or curl."""
    async def generate():
        async for payload in subscribe(event_type=None):
            yield f"data: {payload}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/events/{event_type}")
async def filtered_event_stream(event_type: str):
    """SSE stream filtered to a specific event type (e.g. /api/events/page.loaded)."""
    async def generate():
        async for payload in subscribe(event_type=event_type):
            yield f"data: {payload}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
