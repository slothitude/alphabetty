"""SSE event stream endpoint — external agents and frontend can subscribe to Alphabetty events."""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from core.auth import get_optional_user
from core.events import subscribe
from models.user import User

router = APIRouter(tags=["events"])
logger = logging.getLogger(__name__)


@router.get("/events")
async def event_stream(user: User | None = Depends(get_optional_user)):
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
async def filtered_event_stream(event_type: str, user: User | None = Depends(get_optional_user)):
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
