"""Simple async pub/sub event bus for Alphabetty.

Events flow through the system so external agents and the frontend
can subscribe to things like page loads, research completion, etc.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# Known event types
EVENT_TYPES = {
    "page.loaded",
    "research.done",
    "macro.done",
    "recording.done",
    "youtube.playing",
    "agent.done",
    "tab.created",
    "tab.closed",
    "tool.called",
}

# ─── Subscribers ───
# Each subscriber is an asyncio.Queue
_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
_all_subscribers: list[asyncio.Queue] = []


def emit(event_type: str, data: dict = None) -> None:
    """Publish an event to all matching subscribers (non-blocking)."""
    event = {
        "type": event_type,
        "data": data or {},
        "timestamp": time.time(),
    }
    payload = json.dumps(event)

    # Type-specific subscribers
    dead = []
    for q in _subscribers.get(event_type, []):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _subscribers[event_type].remove(q)

    # Global subscribers
    dead = []
    for q in _all_subscribers:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _all_subscribers.remove(q)

    logger.debug(f"Event emitted: {event_type}")


async def subscribe(event_type: str = None, queue_size: int = 100) -> AsyncIterator[str]:
    """Subscribe to events. If event_type is None, receive all events.
    Yields JSON strings suitable for SSE data lines."""
    q = asyncio.Queue(maxsize=queue_size)
    if event_type:
        _subscribers[event_type].append(q)
    else:
        _all_subscribers.append(q)

    try:
        while True:
            yield await q.get()
    finally:
        if event_type:
            if q in _subscribers[event_type]:
                _subscribers[event_type].remove(q)
        else:
            if q in _all_subscribers:
                _all_subscribers.remove(q)
