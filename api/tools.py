"""REST Tools API — OpenAI-compatible tool definitions and dispatch.

Provides a unified interface for any agent with function calling to use
Alphabetty's capabilities. Calls core functions directly (no HTTP proxy).

Routes:
    GET  /v1/tools       — list all tool definitions (OpenAI format)
    POST /v1/tools/call  — execute a tool by name with arguments
"""

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["tools"])
logger = logging.getLogger(__name__)


# ─── Request/Response Models ───

class ToolCallRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = {}


class ToolResponse(BaseModel):
    tool_name: str
    result: Any
    error: Optional[str] = None


# ─── Tool Definitions (OpenAI function format) ───

TOOL_DEFINITIONS = [
    # Research & Search
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search the web using SearXNG. Returns ranked results with quality scores and domain authority.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string"},
                    "categories": {"type": "string", "description": "Search category: general, news, science, it, files, images, videos", "default": "general"},
                    "max_results": {"type": "integer", "description": "Max results (1-50)", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deep_research",
            "description": "Multi-round deep research pipeline: plan queries, search, extract content, analyze gaps, synthesize comprehensive report.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Research question or topic"},
                    "depth": {"type": "integer", "description": "Research rounds (1-5)", "default": 3},
                    "mode": {"type": "string", "description": "Writing mode: concise, detailed, creative, academic, code", "default": "detailed"},
                    "conversation_id": {"type": "integer", "description": "Optional conversation to attach to"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agent_research",
            "description": "Autonomous AI agent with browsing tools, planning, and reflection. Decides which tools to call across multiple rounds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Task or question for the agent"},
                    "mode": {"type": "string", "description": "Writing mode: concise, detailed, creative, academic, code", "default": "detailed"},
                    "conversation_id": {"type": "integer", "description": "Optional conversation to attach to"},
                },
                "required": ["query"],
            },
        },
    },
    # Chat
    {
        "type": "function",
        "function": {
            "name": "chat",
            "description": "Chat with AI. Returns response with sources and follow-up questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Your message or question"},
                    "conversation_id": {"type": "integer", "description": "Existing conversation ID (creates new if None)"},
                    "mode": {"type": "string", "description": "Response style: concise, detailed, creative, academic, code", "default": "concise"},
                    "search_enabled": {"type": "boolean", "description": "Search web for sources", "default": True},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_conversation",
            "description": "Create a new chat conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Conversation title", "default": "New Chat"},
                    "mode": {"type": "string", "description": "Default response mode", "default": "concise"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_conversations",
            "description": "List all conversations with previews, tags, and metadata.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_conversation",
            "description": "Get a conversation with all messages, sources, and follow-ups.",
            "parameters": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "integer", "description": "The conversation ID"},
                },
                "required": ["conversation_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_conversation",
            "description": "Update a conversation's title or mode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "integer", "description": "The conversation ID"},
                    "title": {"type": "string", "description": "New title"},
                    "mode": {"type": "string", "description": "New mode: concise, detailed, creative, academic, code"},
                },
                "required": ["conversation_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_conversation",
            "description": "Delete a conversation and all its messages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "integer", "description": "The conversation ID to delete"},
                },
                "required": ["conversation_id"],
            },
        },
    },
    # Chrome CDP
    {
        "type": "function",
        "function": {
            "name": "chrome_status",
            "description": "Check Chrome status: running/offline, tab count, user agent, stealth checks.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cdp_tabs",
            "description": "List all open Chrome tabs.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cdp_navigate",
            "description": "Navigate Chrome to a URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to navigate to"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cdp_get_content",
            "description": "Get the text content of the current page.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cdp_get_dom",
            "description": "Get the DOM structure of the current page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "depth": {"type": "integer", "description": "DOM tree depth (1-10)", "default": 3},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cdp_evaluate",
            "description": "Run JavaScript in the browser and return the result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "JavaScript expression to evaluate"},
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cdp_click",
            "description": "Click an element with human-like random offset and timing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the element"},
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cdp_type",
            "description": "Type text into an element with human-like random delays.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the input element"},
                    "text": {"type": "string", "description": "Text to type"},
                },
                "required": ["selector", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cdp_scroll",
            "description": "Scroll the page with human-like behavior.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Horizontal scroll pixels", "default": 0},
                    "y": {"type": "integer", "description": "Vertical scroll pixels", "default": 300},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cdp_screenshot",
            "description": "Take a screenshot. Returns base64-encoded image.",
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {"type": "string", "description": "Image format: png or jpeg", "default": "png"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cdp_extract",
            "description": "Extract data from the current page using a JavaScript expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "JavaScript expression that returns data"},
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cdp_query",
            "description": "Query elements using a CSS selector. Returns matching node IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector to query"},
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cdp_get_cookies",
            "description": "Get all cookies from the browser.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cdp_set_cookie",
            "description": "Set a cookie in the browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Cookie name"},
                    "value": {"type": "string", "description": "Cookie value"},
                    "domain": {"type": "string", "description": "Cookie domain"},
                    "path": {"type": "string", "description": "Cookie path", "default": "/"},
                },
                "required": ["name", "value", "domain"],
            },
        },
    },
    # Knowledge Graph
    {
        "type": "function",
        "function": {
            "name": "graph_search",
            "description": "Full-text search across all stored conversations, messages, and entities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results", "default": 20},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graph_stats",
            "description": "Get knowledge graph statistics.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "entity_graph",
            "description": "Get an entity with neighbors and related conversations from the knowledge graph.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string", "description": "Entity to look up"},
                    "depth": {"type": "integer", "description": "Graph traversal depth (1-3)", "default": 2},
                },
                "required": ["entity_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tags",
            "description": "List all tags in the knowledge graph.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_tag",
            "description": "Add a tag to a conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string", "description": "Tag name"},
                    "conversation_id": {"type": "integer", "description": "Conversation to tag"},
                },
                "required": ["tag", "conversation_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_tag",
            "description": "Remove a tag from a conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string", "description": "Tag to remove"},
                    "conversation_id": {"type": "integer", "description": "Conversation to untag"},
                },
                "required": ["tag", "conversation_id"],
            },
        },
    },
    # Files
    {
        "type": "function",
        "function": {
            "name": "upload_file",
            "description": "Upload a file for analysis. Supports PDF, DOCX, TXT, MD, code files, images.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Name for the uploaded file"},
                    "content_base64": {"type": "string", "description": "Base64-encoded file content"},
                    "query": {"type": "string", "description": "Optional question about the file"},
                },
                "required": ["filename", "content_base64"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_file",
            "description": "Analyze a previously uploaded file with a question.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Name of the previously uploaded file"},
                    "query": {"type": "string", "description": "Question about the file content"},
                },
                "required": ["filename", "query"],
            },
        },
    },
    # Images
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate an image via ComfyUI pipeline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Image description prompt"},
                    "negative_prompt": {"type": "string", "description": "Things to avoid", "default": ""},
                    "width": {"type": "integer", "description": "Image width in pixels", "default": 1024},
                    "height": {"type": "integer", "description": "Image height in pixels", "default": 1024},
                    "steps": {"type": "integer", "description": "Generation steps", "default": 20},
                },
                "required": ["prompt"],
            },
        },
    },
    # Spaces
    {
        "type": "function",
        "function": {
            "name": "create_space",
            "description": "Create a new space for organizing conversations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Space name"},
                    "description": {"type": "string", "description": "Space description", "default": ""},
                    "color": {"type": "string", "description": "Hex color", "default": "#3b82f6"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_spaces",
            "description": "List all spaces with conversation counts.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_space",
            "description": "Get a space with its conversations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "space_id": {"type": "integer", "description": "The space ID"},
                },
                "required": ["space_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_space",
            "description": "Update a space's properties.",
            "parameters": {
                "type": "object",
                "properties": {
                    "space_id": {"type": "integer", "description": "The space ID"},
                    "name": {"type": "string", "description": "New name"},
                    "description": {"type": "string", "description": "New description"},
                    "color": {"type": "string", "description": "New hex color"},
                },
                "required": ["space_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_space",
            "description": "Delete a space (conversations are unlinked, not deleted).",
            "parameters": {
                "type": "object",
                "properties": {
                    "space_id": {"type": "integer", "description": "The space ID to delete"},
                },
                "required": ["space_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_space",
            "description": "Add a conversation to a space.",
            "parameters": {
                "type": "object",
                "properties": {
                    "space_id": {"type": "integer", "description": "Target space ID"},
                    "conversation_id": {"type": "integer", "description": "Conversation to add"},
                },
                "required": ["space_id", "conversation_id"],
            },
        },
    },
    # Export
    {
        "type": "function",
        "function": {
            "name": "export_markdown",
            "description": "Export a conversation as Markdown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "integer", "description": "The conversation to export"},
                },
                "required": ["conversation_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_pdf",
            "description": "Export a conversation as PDF. Returns base64-encoded PDF.",
            "parameters": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "integer", "description": "The conversation to export"},
                },
                "required": ["conversation_id"],
            },
        },
    },
    # Macro Recording
    {
        "type": "function",
        "function": {
            "name": "macro_record_start",
            "description": "Start recording browser actions (navigate, click, type, scroll) as a reusable macro.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the macro"},
                    "url": {"type": "string", "description": "Starting URL (optional)", "default": ""},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "macro_record_stop",
            "description": "Stop macro recording and save it. Returns the saved macro with all captured steps.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "macro_record_browser",
            "description": "Start browser-level recording: injects JS event listeners to capture real user interactions (clicks, typing, scrolling).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the macro"},
                    "url": {"type": "string", "description": "Starting URL (optional)", "default": ""},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "macro_record_stop_browser",
            "description": "Stop browser-level recording, collect captured events, and save as macro.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "macro_play",
            "description": "Replay a saved macro, executing all recorded steps with original timing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "macro_id": {"type": "integer", "description": "ID of the macro to replay"},
                },
                "required": ["macro_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "macro_list",
            "description": "List all saved macros with step counts and durations.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    # Screen Recording
    {
        "type": "function",
        "function": {
            "name": "screen_record_start",
            "description": "Start recording the browser screen as a video via CDP screencast.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fps": {"type": "integer", "description": "Frames per second (1-30)", "default": 10},
                    "quality": {"type": "integer", "description": "JPEG quality (10-100)", "default": 80},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screen_record_stop",
            "description": "Stop screen recording and return compiled video as base64.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    # YouTube
    {
        "type": "function",
        "function": {
            "name": "youtube_play",
            "description": "Search YouTube and navigate Chrome to the first matching video.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "YouTube search query"},
                },
                "required": ["query"],
            },
        },
    },
]


# ─── Tool Handlers (direct core calls, no HTTP proxy) ───

async def _collect_sse(gen) -> dict:
    """Consume an SSE generator and return aggregated result."""
    tokens = []
    final = {}
    async for chunk in gen:
        # chunks are raw SSE strings "data: {...}\n\n"
        if not chunk.startswith("data: "):
            continue
        payload = chunk[6:]
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        etype = event.get("type")
        if etype == "token":
            tokens.append(event.get("content", ""))
        elif etype == "done":
            final = {k: v for k, v in event.items() if k != "type"}
        elif etype == "error":
            return {"error": event.get("error")}
    result = {"response": "".join(tokens)}
    result.update(final)
    return result


async def _handle_search(**kwargs) -> dict:
    from core.searxng import search
    from core.source_citer import format_sources
    results = await search(
        kwargs["query"],
        categories=kwargs.get("categories", "general"),
        max_results=kwargs.get("max_results", 10),
    )
    return {"query": kwargs["query"], "results": format_sources(results)}


async def _handle_deep_research(**kwargs) -> dict:
    from api.research import ResearchRequest, deep_research
    req = ResearchRequest(
        query=kwargs["query"],
        conversation_id=kwargs.get("conversation_id"),
        depth=kwargs.get("depth", 3),
        mode=kwargs.get("mode", "detailed"),
    )
    response = await deep_research(req)
    # response is a StreamingResponse — collect SSE
    from fastapi.responses import StreamingResponse
    if isinstance(response, StreamingResponse):
        chunks = []
        async def collect():
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        await collect()
        gen = (c for c in chunks)
        return await _collect_sse(gen)
    return response


async def _handle_agent_research(**kwargs) -> dict:
    from api.agent import AgentRequest, agent_chat
    req = AgentRequest(
        query=kwargs["query"],
        conversation_id=kwargs.get("conversation_id"),
        mode=kwargs.get("mode", "detailed"),
    )
    response = await agent_chat(req)
    from fastapi.responses import StreamingResponse
    if isinstance(response, StreamingResponse):
        chunks = []
        async def collect():
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        await collect()
        gen = (c for c in chunks)
        return await _collect_sse(gen)
    return response


async def _handle_chat(**kwargs) -> dict:
    from api.chat import ChatRequest, chat
    req = ChatRequest(
        query=kwargs["query"],
        conversation_id=kwargs.get("conversation_id"),
        mode=kwargs.get("mode", "concise"),
        search_enabled=kwargs.get("search_enabled", True),
    )
    response = await chat(req)
    from fastapi.responses import StreamingResponse
    if isinstance(response, StreamingResponse):
        chunks = []
        async def collect():
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        await collect()
        gen = (c for c in chunks)
        return await _collect_sse(gen)
    return response


async def _handle_create_conversation(**kwargs) -> dict:
    from app import async_session
    from models.conversation import Conversation
    async with async_session() as db:
        conv = Conversation(
            title=kwargs.get("title", "New Chat"),
            mode=kwargs.get("mode", "concise"),
        )
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        return {"id": conv.id, "title": conv.title, "mode": conv.mode}


async def _handle_list_conversations(**kwargs) -> list:
    from app import async_session
    from sqlalchemy import select
    from models.conversation import Conversation, Message
    from models.graph import Tag, ConversationTag
    async with async_session() as db:
        result = await db.execute(
            select(Conversation).order_by(Conversation.updated_at.desc())
        )
        convs = result.scalars().all()
        out = []
        for c in convs:
            preview = ""
            first = await db.execute(
                select(Message).where(
                    Message.conversation_id == c.id,
                    Message.role == "assistant",
                ).order_by(Message.id).limit(1)
            )
            msg = first.scalar_one_or_none()
            if msg and msg.content:
                preview = msg.content[:100].replace("\n", " ")
            tags_result = await db.execute(
                select(Tag).join(ConversationTag, ConversationTag.tag_id == Tag.id)
                .where(ConversationTag.conversation_id == c.id)
            )
            tags = [{"id": t.id, "name": t.name, "color": t.color} for t in tags_result.scalars().all()]
            out.append({
                "id": c.id, "title": c.title, "mode": c.mode,
                "updated_at": c.updated_at.isoformat(), "preview": preview, "tags": tags,
            })
        return out


async def _handle_get_conversation(**kwargs) -> dict:
    from app import async_session
    from sqlalchemy import select
    from models.conversation import Conversation, Message
    from models.graph import Tag, ConversationTag
    conv_id = kwargs["conversation_id"]
    async with async_session() as db:
        result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
        conv = result.scalar_one_or_none()
        if not conv:
            return {"error": "Not found"}
        msgs = await db.execute(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
        )
        messages = msgs.scalars().all()
        tags_result = await db.execute(
            select(Tag).join(ConversationTag, ConversationTag.tag_id == Tag.id)
            .where(ConversationTag.conversation_id == conv_id)
        )
        tags = [{"id": t.id, "name": t.name, "color": t.color} for t in tags_result.scalars().all()]
        return {
            "id": conv.id, "title": conv.title, "mode": conv.mode, "tags": tags,
            "messages": [
                {"id": m.id, "role": m.role, "content": m.content, "sources": m.sources or [], "follow_ups": m.follow_ups or []}
                for m in messages
            ],
        }


async def _handle_update_conversation(**kwargs) -> dict:
    from app import async_session
    from sqlalchemy import select
    from models.conversation import Conversation
    conv_id = kwargs["conversation_id"]
    async with async_session() as db:
        result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
        conv = result.scalar_one_or_none()
        if not conv:
            return {"error": "Not found"}
        if kwargs.get("title"):
            conv.title = kwargs["title"]
        if kwargs.get("mode"):
            conv.mode = kwargs["mode"]
        await db.commit()
        return {"id": conv.id, "title": conv.title, "mode": conv.mode}


async def _handle_delete_conversation(**kwargs) -> dict:
    from app import async_session
    from sqlalchemy import select
    from models.conversation import Conversation
    conv_id = kwargs["conversation_id"]
    async with async_session() as db:
        result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
        conv = result.scalar_one_or_none()
        if conv:
            await db.delete(conv)
            await db.commit()
    return {"ok": True}


# ─── Chrome CDP Handlers ───

async def _handle_chrome_status(**kwargs) -> dict:
    from core.cdp_bridge import cdp
    try:
        tabs = await cdp.get_tabs()
        checks = await cdp.is_undetected()
        ua = await cdp.user_agent()
        return {"status": "running", "tab_count": len(tabs), "user_agent": ua, "stealth": checks}
    except Exception as e:
        return {"status": "offline", "error": str(e)}


async def _handle_cdp_tabs(**kwargs) -> dict:
    from core.cdp_bridge import cdp
    return {"tabs": await cdp.get_tabs()}


async def _handle_cdp_navigate(**kwargs) -> dict:
    from core.cdp_bridge import cdp
    return {"status": "ok", "result": await cdp.navigate(kwargs["url"])}


async def _handle_cdp_get_content(**kwargs) -> dict:
    from core.cdp_bridge import cdp
    return {"content": await cdp.get_content()}


async def _handle_cdp_get_dom(**kwargs) -> dict:
    from core.cdp_bridge import cdp
    return {"dom": await cdp.get_dom(kwargs.get("depth", 3))}


async def _handle_cdp_evaluate(**kwargs) -> dict:
    from core.cdp_bridge import cdp
    return {"result": await cdp.evaluate(kwargs["expression"])}


async def _handle_cdp_click(**kwargs) -> dict:
    from core.cdp_bridge import cdp
    return await cdp.click(kwargs["selector"])


async def _handle_cdp_type(**kwargs) -> dict:
    from core.cdp_bridge import cdp
    return await cdp.type_text(kwargs["selector"], kwargs["text"])


async def _handle_cdp_scroll(**kwargs) -> dict:
    from core.cdp_bridge import cdp
    return await cdp.scroll(kwargs.get("x", 0), kwargs.get("y", 300))


async def _handle_cdp_screenshot(**kwargs) -> dict:
    import base64
    from core.cdp_bridge import cdp
    data = await cdp.screenshot(kwargs.get("format", "png"))
    return {"data_base64": base64.b64encode(data).decode(), "format": kwargs.get("format", "png"), "size": len(data)}


async def _handle_cdp_extract(**kwargs) -> dict:
    from core.cdp_bridge import cdp
    return {"data": await cdp.evaluate(kwargs["expression"])}


async def _handle_cdp_query(**kwargs) -> dict:
    from core.cdp_bridge import cdp
    return {"nodeId": await cdp.query_selector(kwargs["selector"])}


async def _handle_cdp_get_cookies(**kwargs) -> dict:
    from core.cdp_bridge import cdp
    return {"cookies": await cdp.get_cookies()}


async def _handle_cdp_set_cookie(**kwargs) -> dict:
    from core.cdp_bridge import cdp
    return {"status": "ok", "result": await cdp.set_cookie(
        kwargs["name"], kwargs["value"], kwargs["domain"], kwargs.get("path", "/")
    )}


# ─── Knowledge Graph Handlers ───

async def _handle_graph_search(**kwargs) -> dict:
    from core.graph import search_local
    return await search_local(kwargs["query"], kwargs.get("limit", 20))


async def _handle_graph_stats(**kwargs) -> dict:
    from core.graph import get_all_stats
    return await get_all_stats()


async def _handle_entity_graph(**kwargs) -> dict:
    from core.graph import get_entity_graph
    return await get_entity_graph(kwargs["entity_name"], kwargs.get("depth", 2))


async def _handle_list_tags(**kwargs):
    from core.graph import list_tags
    return await list_tags()


async def _handle_add_tag(**kwargs) -> dict:
    from core.graph import tag_conversation
    return await tag_conversation(kwargs["conversation_id"], kwargs["tag"])


async def _handle_remove_tag(**kwargs) -> dict:
    from core.graph import untag_conversation
    await untag_conversation(kwargs["conversation_id"], kwargs["tag"])
    return {"ok": True}


# ─── File Handlers ───

async def _handle_upload_file(**kwargs) -> dict:
    import base64
    from pathlib import Path
    from config import settings
    from core.llm import call_llm

    filename = kwargs["filename"]
    content_b64 = kwargs["content_base64"]
    query = kwargs.get("query")

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / filename
    file_path.write_bytes(base64.b64decode(content_b64))

    result = {"filename": filename, "size": file_path.stat().st_size, "path": str(file_path)}

    if query:
        from api.files import extract_text_from_file
        text = await extract_text_from_file(file_path, filename)
        if text and not text.startswith("[Unsupported"):
            messages = [
                {"role": "system", "content": "You are analyzing a file. Answer the user's question based on the file content."},
                {"role": "user", "content": f"File: {filename}\n\nContent:\n{text[:50000]}\n\nQuestion: {query}"},
            ]
            result["analysis"] = await call_llm(messages)
        else:
            result["analysis"] = text

    return result


async def _handle_analyze_file(**kwargs) -> dict:
    from pathlib import Path
    from config import settings
    from core.llm import call_llm
    from api.files import extract_text_from_file

    filename = kwargs["filename"]
    file_path = Path(settings.upload_dir) / filename
    if not file_path.exists():
        return {"error": "File not found"}

    text = await extract_text_from_file(file_path, filename)
    messages = [
        {"role": "system", "content": "You are analyzing a file. Answer the user's question based on the file content."},
        {"role": "user", "content": f"File: {filename}\n\nContent:\n{text[:50000]}\n\nQuestion: {kwargs['query']}"},
    ]
    return {"analysis": await call_llm(messages)}


# ─── Image Handler ───

async def _handle_generate_image(**kwargs) -> dict:
    import httpx
    from config import settings
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            resp = await client.post(
                f"{settings.router_url}/api/generate",
                json={
                    "prompt": kwargs["prompt"],
                    "negative_prompt": kwargs.get("negative_prompt", ""),
                    "width": kwargs.get("width", 1024),
                    "height": kwargs.get("height", 1024),
                    "steps": kwargs.get("steps", 20),
                },
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"error": str(e), "status": "failed"}


# ─── Space Handlers ───

async def _handle_create_space(**kwargs) -> dict:
    from app import async_session
    from models.space import Space
    async with async_session() as db:
        space = Space(
            name=kwargs["name"],
            description=kwargs.get("description", ""),
            color=kwargs.get("color", "#3b82f6"),
        )
        db.add(space)
        await db.commit()
        await db.refresh(space)
        return {"id": space.id, "name": space.name, "description": space.description, "color": space.color}


async def _handle_list_spaces(**kwargs) -> list:
    from app import async_session
    from sqlalchemy import select
    from models.conversation import Conversation
    from models.space import Space
    async with async_session() as db:
        result = await db.execute(select(Space).order_by(Space.created_at.desc()))
        spaces = result.scalars().all()
        out = []
        for s in spaces:
            convs = await db.execute(select(Conversation).where(Conversation.space_id == s.id))
            conv_count = len(convs.scalars().all())
            out.append({"id": s.id, "name": s.name, "description": s.description, "color": s.color, "conversation_count": conv_count})
        return out


async def _handle_get_space(**kwargs) -> dict:
    from app import async_session
    from sqlalchemy import select
    from models.conversation import Conversation
    from models.space import Space
    space_id = kwargs["space_id"]
    async with async_session() as db:
        result = await db.execute(select(Space).where(Space.id == space_id))
        space = result.scalar_one_or_none()
        if not space:
            return {"error": "Not found"}
        convs = await db.execute(
            select(Conversation).where(Conversation.space_id == space_id).order_by(Conversation.updated_at.desc())
        )
        conversations = convs.scalars().all()
        return {
            "id": space.id, "name": space.name, "description": space.description, "color": space.color,
            "conversations": [
                {"id": c.id, "title": c.title, "mode": c.mode, "updated_at": c.updated_at.isoformat()}
                for c in conversations
            ],
        }


async def _handle_update_space(**kwargs) -> dict:
    from app import async_session
    from sqlalchemy import select
    from models.space import Space
    space_id = kwargs["space_id"]
    async with async_session() as db:
        result = await db.execute(select(Space).where(Space.id == space_id))
        space = result.scalar_one_or_none()
        if not space:
            return {"error": "Not found"}
        if kwargs.get("name"):
            space.name = kwargs["name"]
        if kwargs.get("description") is not None:
            space.description = kwargs["description"]
        if kwargs.get("color"):
            space.color = kwargs["color"]
        await db.commit()
        return {"id": space.id, "name": space.name, "description": space.description, "color": space.color}


async def _handle_delete_space(**kwargs) -> dict:
    from app import async_session
    from sqlalchemy import select
    from models.conversation import Conversation
    from models.space import Space
    space_id = kwargs["space_id"]
    async with async_session() as db:
        result = await db.execute(select(Space).where(Space.id == space_id))
        space = result.scalar_one_or_none()
        if space:
            convs = await db.execute(select(Conversation).where(Conversation.space_id == space_id))
            for c in convs.scalars().all():
                c.space_id = None
            await db.delete(space)
            await db.commit()
    return {"ok": True}


async def _handle_add_to_space(**kwargs) -> dict:
    from app import async_session
    from sqlalchemy import select
    from models.conversation import Conversation
    async with async_session() as db:
        result = await db.execute(select(Conversation).where(Conversation.id == kwargs["conversation_id"]))
        conv = result.scalar_one_or_none()
        if conv:
            conv.space_id = kwargs["space_id"]
            await db.commit()
    return {"ok": True}


# ─── Export Handlers ───

async def _handle_export_markdown(**kwargs) -> dict:
    from app import async_session
    from sqlalchemy import select
    from models.conversation import Conversation, Message
    from datetime import datetime, timezone
    import re
    conv_id = kwargs["conversation_id"]
    async with async_session() as db:
        result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
        conv = result.scalar_one_or_none()
        if not conv:
            return {"error": "Not found"}
        msgs = await db.execute(select(Message).where(Message.conversation_id == conv_id).order_by(Message.id))
        messages = msgs.scalars().all()

    lines = [f"# {conv.title}\n", f"Exported: {datetime.now(timezone.utc).isoformat()}\n\n---\n"]
    for m in messages:
        role_label = "**You**" if m.role == "user" else "**Alphabetty**"
        lines.append(f"\n### {role_label}\n\n{m.content}\n")
        if m.sources:
            lines.append("\n**Sources:**\n")
            for s in m.sources:
                idx = s.get("index", "?")
                lines.append(f"- [{idx}] [{s.get('title', 'Untitled')}]({s.get('url', '')}) — {s.get('domain', '')}")
        if m.follow_ups:
            lines.append("\n**Follow-up questions:**\n")
            for fq in m.follow_ups:
                lines.append(f"- {fq}")
        lines.append("\n---\n")

    return {"markdown": "\n".join(lines)}


async def _handle_export_pdf(**kwargs) -> dict:
    import base64
    from app import async_session
    from sqlalchemy import select
    from models.conversation import Conversation, Message
    conv_id = kwargs["conversation_id"]
    async with async_session() as db:
        result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
        conv = result.scalar_one_or_none()
        if not conv:
            return {"error": "Not found"}
        msgs = await db.execute(select(Message).where(Message.conversation_id == conv_id).order_by(Message.id))
        messages = msgs.scalars().all()

    import io
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.units import inch

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.75*inch, bottomMargin=0.75*inch)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle("ConvTitle", parent=styles["Title"], fontSize=18)
    story.append(Paragraph(conv.title, title_style))
    story.append(Spacer(1, 0.2 * inch))
    story.append(HRFlowable(width="100%"))
    story.append(Spacer(1, 0.2 * inch))

    for m in messages:
        role = "You" if m.role == "user" else "Alphabetty"
        role_style = ParagraphStyle("Role", parent=styles["Heading2"], fontSize=13)
        story.append(Paragraph(role, role_style))
        content = m.content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
        body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=14)
        story.append(Paragraph(content, body_style))
        if m.sources:
            story.append(Spacer(1, 0.1 * inch))
            sources_text = "Sources: " + ", ".join(f"[{s.get('index', '?')}] {s.get('title', '')}" for s in m.sources)
            src_style = ParagraphStyle("Sources", parent=styles["Normal"], fontSize=8, textColor="gray")
            story.append(Paragraph(sources_text.replace("&", "&amp;").replace("<", "&lt;"), src_style))
        story.append(Spacer(1, 0.2 * inch))
        story.append(HRFlowable(width="80%", color="lightgray"))
        story.append(Spacer(1, 0.1 * inch))

    doc.build(story)
    return {"data_base64": base64.b64encode(buffer.getvalue()).decode(), "size": len(buffer.getvalue())}


# ─── Macro & Screen Recording Handlers ───

async def _handle_macro_record_start(**kwargs) -> dict:
    from core.macro import recorder
    return recorder.start(kwargs["name"], kwargs.get("url", ""))


async def _handle_macro_record_stop(**kwargs) -> dict:
    from core.macro import recorder
    return await recorder.stop()


async def _handle_macro_record_browser(**kwargs) -> dict:
    from core.macro import recorder
    return await recorder.start_browser(kwargs["name"], kwargs.get("url", ""))


async def _handle_macro_record_stop_browser(**kwargs) -> dict:
    from core.macro import recorder
    return await recorder.stop_browser()


async def _handle_macro_play(**kwargs) -> dict:
    from core.macro import recorder
    return await recorder.play(kwargs["macro_id"])


async def _handle_macro_list(**kwargs) -> dict:
    from core.macro import recorder
    return {"macros": await recorder.list_macros()}


async def _handle_screen_record_start(**kwargs) -> dict:
    from core.recording import screen_recorder
    return await screen_recorder.start(fps=kwargs.get("fps", 10), quality=kwargs.get("quality", 80))


async def _handle_screen_record_stop(**kwargs) -> dict:
    from core.recording import screen_recorder
    return await screen_recorder.stop()


async def _handle_youtube_play(**kwargs) -> dict:
    from core.cdp_bridge import cdp
    import asyncio
    from urllib.parse import urlparse, parse_qs

    query = kwargs["query"]

    def _yt_search():
        import yt_dlp
        ydl_opts = {"quiet": True, "extract_flat": True, "default_search": "ytsearch1"}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            results = ydl.extract_info(f"ytsearch1:{query}", download=False)
            entries = results.get("entries", [])
            if entries:
                return entries[0].get("url") or entries[0].get("webpage_url")
        return None

    video_url = await asyncio.get_event_loop().run_in_executor(None, _yt_search)

    if not video_url:
        return {"error": f"No video found for: {query}"}

    if not video_url.startswith("http"):
        video_url = f"https://www.youtube.com{video_url}"

    parsed = urlparse(video_url)
    video_id = parse_qs(parsed.query).get("v", [None])[0] or parsed.path.split("/")[-1]

    await cdp.navigate(video_url)

    return {"status": "playing", "query": query, "video_url": video_url, "video_id": video_id}


# ─── Handler Registry ───

TOOL_HANDLERS = {
    "search": _handle_search,
    "deep_research": _handle_deep_research,
    "agent_research": _handle_agent_research,
    "chat": _handle_chat,
    "create_conversation": _handle_create_conversation,
    "list_conversations": _handle_list_conversations,
    "get_conversation": _handle_get_conversation,
    "update_conversation": _handle_update_conversation,
    "delete_conversation": _handle_delete_conversation,
    "chrome_status": _handle_chrome_status,
    "cdp_tabs": _handle_cdp_tabs,
    "cdp_navigate": _handle_cdp_navigate,
    "cdp_get_content": _handle_cdp_get_content,
    "cdp_get_dom": _handle_cdp_get_dom,
    "cdp_evaluate": _handle_cdp_evaluate,
    "cdp_click": _handle_cdp_click,
    "cdp_type": _handle_cdp_type,
    "cdp_scroll": _handle_cdp_scroll,
    "cdp_screenshot": _handle_cdp_screenshot,
    "cdp_extract": _handle_cdp_extract,
    "cdp_query": _handle_cdp_query,
    "cdp_get_cookies": _handle_cdp_get_cookies,
    "cdp_set_cookie": _handle_cdp_set_cookie,
    "graph_search": _handle_graph_search,
    "graph_stats": _handle_graph_stats,
    "entity_graph": _handle_entity_graph,
    "list_tags": _handle_list_tags,
    "add_tag": _handle_add_tag,
    "remove_tag": _handle_remove_tag,
    "upload_file": _handle_upload_file,
    "analyze_file": _handle_analyze_file,
    "generate_image": _handle_generate_image,
    "create_space": _handle_create_space,
    "list_spaces": _handle_list_spaces,
    "get_space": _handle_get_space,
    "update_space": _handle_update_space,
    "delete_space": _handle_delete_space,
    "add_to_space": _handle_add_to_space,
    "export_markdown": _handle_export_markdown,
    "export_pdf": _handle_export_pdf,
    "macro_record_start": _handle_macro_record_start,
    "macro_record_stop": _handle_macro_record_stop,
    "macro_record_browser": _handle_macro_record_browser,
    "macro_record_stop_browser": _handle_macro_record_stop_browser,
    "macro_play": _handle_macro_play,
    "macro_list": _handle_macro_list,
    "screen_record_start": _handle_screen_record_start,
    "screen_record_stop": _handle_screen_record_stop,
    "youtube_play": _handle_youtube_play,
}


# ─── Routes ───

@router.get("/v1/tools")
async def list_tools():
    """List all available tools in OpenAI function calling format."""
    return {"tools": TOOL_DEFINITIONS}


@router.post("/v1/tools/call")
async def call_tool(call: ToolCallRequest):
    """Execute a tool by name with the given arguments."""
    handler = TOOL_HANDLERS.get(call.tool_name)
    if not handler:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {call.tool_name}")

    try:
        result = await handler(**call.arguments)
        return ToolResponse(tool_name=call.tool_name, result=result)
    except Exception as e:
        logger.error(f"Tool '{call.tool_name}' failed: {e}", exc_info=True)
        return ToolResponse(tool_name=call.tool_name, result=None, error=str(e))
