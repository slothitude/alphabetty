"""Alphabetty MCP Server — stdio transport, HTTP proxy to localhost:7700.

Provides all Alphabetty capabilities as MCP tools for external LLM agents
(Claude Code, Cursor, etc). Each tool proxies HTTP requests to the running
Alphabetty FastAPI app.
"""

import asyncio
import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import httpx
from fastmcp import FastMCP

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("alphabetty-mcp")

BASE = os.environ.get("ALPHABETTY_URL", "http://localhost:7700")
TIMEOUT = httpx.Timeout(300.0, connect=10.0)

mcp = FastMCP("alphabetty", instructions="Alphabetty AI research assistant — search, chat, browse, research, knowledge graph, and more.")


# ─── Helpers ───

async def _get(path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{BASE}{path}", params=params)
        r.raise_for_status()
        return r.json()


async def _post(path: str, json_data: dict | None = None, files=None) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(f"{BASE}{path}", json=json_data, files=files)
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "application/json" in ct:
            return r.json()
        # Binary response (screenshot, PDF, markdown)
        return {"content_type": ct, "data_base64": base64.b64encode(r.content).decode(), "size": len(r.content)}


async def _patch(path: str, json_data: dict) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.patch(f"{BASE}{path}", json=json_data)
        r.raise_for_status()
        return r.json()


async def _delete(path: str) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.delete(f"{BASE}{path}")
        r.raise_for_status()
        return r.json()


async def _sse_post(path: str, json_data: dict) -> str:
    """POST to an SSE endpoint, consume the full stream, return aggregated result."""
    tokens = []
    final_event = {}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        async with client.stream("POST", f"{BASE}{path}", json=json_data) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                etype = event.get("type")
                if etype == "token":
                    tokens.append(event.get("content", ""))
                elif etype == "done":
                    final_event = event
                elif etype == "error":
                    return json.dumps({"error": event.get("error")})
                elif etype in ("progress", "agent_thinking", "plan", "tool_call", "tool_result", "reflection"):
                    # Progress events — just collect for final output
                    pass

    result = {"response": "".join(tokens)}
    if final_event:
        result.update({k: v for k, v in final_event.items() if k != "type"})
    return json.dumps(result)


# ─── Research & Search ───

@mcp.tool()
async def search(query: str, categories: str = "general", max_results: int = 10) -> str:
    """Search the web using SearXNG. Returns ranked results with quality scores and domain authority.

    Args:
        query: Search query string
        categories: Search category (general, news, science, it, files, images, videos)
        max_results: Maximum number of results to return (1-50)
    """
    return json.dumps(await _get("/api/search", {"q": query, "categories": categories, "max_results": max_results}))


@mcp.tool()
async def deep_research(query: str, depth: int = 3, mode: str = "detailed") -> str:
    """Multi-round deep research pipeline: plan search queries, execute searches, extract content from sources, analyze gaps, and synthesize a comprehensive report.

    Args:
        query: Research question or topic to investigate
        depth: Number of research rounds (1-5). More rounds = more thorough but slower
        mode: Writing mode - concise, detailed, creative, academic, or code
    """
    return await _sse_post("/api/research", {"query": query, "depth": depth, "mode": mode})


@mcp.tool()
async def agent_research(query: str, mode: str = "detailed") -> str:
    """Autonomous AI agent with browsing tools, planning, and reflection. The agent decides which tools to call (search, browse, click, extract), runs multiple rounds, and produces a final answer.

    Args:
        query: Task or question for the agent to research
        mode: Writing mode - concise, detailed, creative, academic, or code
    """
    return await _sse_post("/api/agent", {"query": query, "mode": mode})


# ─── Chat ───

@mcp.tool()
async def chat(query: str, conversation_id: int | None = None,
               mode: str = "concise", search_enabled: bool = True) -> str:
    """Chat with AI. Sends a message and returns the full response with sources and follow-up questions.

    Args:
        query: Your message or question
        conversation_id: Existing conversation ID to continue (creates new if None)
        mode: Response style - concise, detailed, creative, academic, or code
        search_enabled: Whether to search the web for sources before responding
    """
    return await _sse_post("/api/chat", {
        "query": query, "conversation_id": conversation_id,
        "mode": mode, "search_enabled": search_enabled,
    })


@mcp.tool()
async def create_conversation(title: str = "New Chat", mode: str = "concise") -> str:
    """Create a new chat conversation.

    Args:
        title: Conversation title
        mode: Default response mode - concise, detailed, creative, academic, or code
    """
    return json.dumps(await _post("/api/conversations", {"title": title, "mode": mode}))


@mcp.tool()
async def list_conversations() -> str:
    """List all conversations with previews, tags, and metadata."""
    return json.dumps(await _get("/api/conversations"))


@mcp.tool()
async def get_conversation(conversation_id: int) -> str:
    """Get a conversation with all its messages, sources, and follow-ups.

    Args:
        conversation_id: The conversation ID
    """
    return json.dumps(await _get(f"/api/conversations/{conversation_id}"))


@mcp.tool()
async def update_conversation(conversation_id: int, title: str | None = None,
                              mode: str | None = None) -> str:
    """Update a conversation's title or mode.

    Args:
        conversation_id: The conversation ID
        title: New title (optional)
        mode: New mode - concise, detailed, creative, academic, or code (optional)
    """
    data = {}
    if title is not None:
        data["title"] = title
    if mode is not None:
        data["mode"] = mode
    return json.dumps(await _patch(f"/api/conversations/{conversation_id}", data))


@mcp.tool()
async def delete_conversation(conversation_id: int) -> str:
    """Delete a conversation and all its messages.

    Args:
        conversation_id: The conversation ID to delete
    """
    return json.dumps(await _delete(f"/api/conversations/{conversation_id}"))


# ─── Chrome CDP ───

@mcp.tool()
async def chrome_status() -> str:
    """Check Chrome status: running/offline, tab count, user agent, stealth checks."""
    return json.dumps(await _get("/api/cdp/status"))


@mcp.tool()
async def cdp_tabs() -> str:
    """List all open Chrome tabs."""
    return json.dumps(await _get("/api/cdp/tabs"))


@mcp.tool()
async def cdp_navigate(url: str) -> str:
    """Navigate Chrome to a URL.

    Args:
        url: The URL to navigate to
    """
    return json.dumps(await _post("/api/cdp/navigate", {"url": url}))


@mcp.tool()
async def cdp_get_content() -> str:
    """Get the text content of the current page."""
    return json.dumps(await _get("/api/cdp/content"))


@mcp.tool()
async def cdp_get_dom(depth: int = 3) -> str:
    """Get the DOM structure of the current page.

    Args:
        depth: DOM tree depth to return (1-10)
    """
    return json.dumps(await _get("/api/cdp/dom", {"depth": depth}))


@mcp.tool()
async def cdp_evaluate(expression: str) -> str:
    """Run JavaScript in the browser and return the result.

    Args:
        expression: JavaScript expression to evaluate
    """
    return json.dumps(await _post("/api/cdp/evaluate", {"expression": expression}))


@mcp.tool()
async def cdp_click(selector: str) -> str:
    """Click an element on the page with human-like random offset and timing.

    Args:
        selector: CSS selector for the element to click
    """
    return json.dumps(await _post("/api/cdp/click", {"selector": selector}))


@mcp.tool()
async def cdp_type(selector: str, text: str) -> str:
    """Type text into an element with human-like random delays between keystrokes.

    Args:
        selector: CSS selector for the input element
        text: Text to type
    """
    return json.dumps(await _post("/api/cdp/type", {"selector": selector, "text": text}))


@mcp.tool()
async def cdp_scroll(x: int = 0, y: int = 300) -> str:
    """Scroll the page with human-like behavior.

    Args:
        x: Horizontal scroll pixels
        y: Vertical scroll pixels
    """
    return json.dumps(await _post("/api/cdp/scroll", {"x": x, "y": y}))


@mcp.tool()
async def cdp_screenshot(format: str = "png") -> str:
    """Take a screenshot of the current page. Returns base64-encoded image.

    Args:
        format: Image format - png or jpeg
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{BASE}/api/cdp/screenshot", params={"format": format})
        r.raise_for_status()
        return base64.b64encode(r.content).decode()


@mcp.tool()
async def cdp_extract(expression: str) -> str:
    """Extract data from the current page using a JavaScript expression.

    Args:
        expression: JavaScript expression that returns data to extract
    """
    return json.dumps(await _post("/api/cdp/extract", {"expression": expression}))


@mcp.tool()
async def cdp_query(selector: str) -> str:
    """Query elements on the page using a CSS selector. Returns matching node IDs.

    Args:
        selector: CSS selector to query
    """
    return json.dumps(await _post("/api/cdp/query", {"selector": selector}))


@mcp.tool()
async def cdp_get_cookies() -> str:
    """Get all cookies from the browser."""
    return json.dumps(await _get("/api/cdp/cookies"))


@mcp.tool()
async def cdp_set_cookie(name: str, value: str, domain: str, path: str = "/") -> str:
    """Set a cookie in the browser.

    Args:
        name: Cookie name
        value: Cookie value
        domain: Cookie domain
        path: Cookie path (default /)
    """
    return json.dumps(await _post("/api/cdp/cookies", {"name": name, "value": value, "domain": domain, "path": path}))


# ─── Macro Recording ───

@mcp.tool()
async def macro_record_start(name: str, url: str = "") -> str:
    """Start recording browser actions (navigate, click, type, scroll) as a reusable macro.

    Args:
        name: Name for the macro
        url: Starting URL (optional)
    """
    return json.dumps(await _post("/api/cdp/macro/record/start", {"name": name, "url": url}))


@mcp.tool()
async def macro_record_stop() -> str:
    """Stop macro recording and save it. Returns the saved macro with all captured steps."""
    return json.dumps(await _post("/api/cdp/macro/record/stop"))


@mcp.tool()
async def macro_record_browser(name: str, url: str = "") -> str:
    """Start browser-level recording: injects JS event listeners to capture real user interactions.

    Args:
        name: Name for the macro
        url: Starting URL (optional)
    """
    return json.dumps(await _post("/api/cdp/macro/browser/start", {"name": name, "url": url}))


@mcp.tool()
async def macro_record_stop_browser() -> str:
    """Stop browser-level recording, collect captured events, and save as macro."""
    return json.dumps(await _post("/api/cdp/macro/browser/stop"))


@mcp.tool()
async def macro_play(macro_id: int) -> str:
    """Replay a saved macro, executing all recorded steps with original timing.

    Args:
        macro_id: ID of the macro to replay
    """
    return json.dumps(await _post("/api/cdp/macro/play", {"macro_id": macro_id}))


@mcp.tool()
async def macro_list() -> str:
    """List all saved macros with step counts and durations."""
    return json.dumps(await _get("/api/cdp/macro/list"))


# ─── Screen Recording ───

@mcp.tool()
async def screen_record_start(fps: int = 10, quality: int = 80) -> str:
    """Start recording the browser screen as a video via CDP screencast.

    Args:
        fps: Frames per second (1-30)
        quality: JPEG quality (10-100)
    """
    return json.dumps(await _post("/api/cdp/screen/record/start", {"fps": fps, "quality": quality}))


@mcp.tool()
async def screen_record_stop() -> str:
    """Stop screen recording and return compiled video as base64."""
    return json.dumps(await _post("/api/cdp/screen/record/stop"))


# ─── YouTube ───

@mcp.tool()
async def youtube_play(query: str) -> str:
    """Search YouTube and navigate Chrome to the first matching video.

    Args:
        query: YouTube search query
    """
    return json.dumps(await _post("/api/cdp/youtube", {"query": query}))


# ─── Knowledge Graph ───

@mcp.tool()
async def graph_search(query: str, limit: int = 20) -> str:
    """Full-text search across all stored conversations, messages, and entities in the knowledge graph.

    Args:
        query: Search query
        limit: Maximum results to return
    """
    return json.dumps(await _get("/api/graph/search", {"q": query, "limit": limit}))


@mcp.tool()
async def graph_stats() -> str:
    """Get knowledge graph statistics: entity counts, conversation counts, tag counts, etc."""
    return json.dumps(await _get("/api/graph/stats"))


@mcp.tool()
async def entity_graph(entity_name: str, depth: int = 2) -> str:
    """Get an entity with its neighbors and related conversations from the knowledge graph.

    Args:
        entity_name: Name of the entity to look up
        depth: Graph traversal depth (1-3)
    """
    return json.dumps(await _get(f"/api/graph/entity/{entity_name}", {"depth": depth}))


@mcp.tool()
async def list_tags() -> str:
    """List all tags in the knowledge graph."""
    return json.dumps(await _get("/api/tags"))


@mcp.tool()
async def add_tag(tag: str, conversation_id: int) -> str:
    """Add a tag to a conversation.

    Args:
        tag: Tag name
        conversation_id: Conversation to tag
    """
    return json.dumps(await _post("/api/tags/add", {"tag": tag, "conversation_id": conversation_id}))


@mcp.tool()
async def remove_tag(tag: str, conversation_id: int) -> str:
    """Remove a tag from a conversation.

    Args:
        tag: Tag name to remove
        conversation_id: Conversation to untag
    """
    return json.dumps(await _post("/api/tags/remove", {"tag": tag, "conversation_id": conversation_id}))


# ─── Files ───

@mcp.tool()
async def upload_file(file_path: str, query: str | None = None) -> str:
    """Upload a file from a local path for analysis. Supports PDF, DOCX, TXT, MD, code files, and images.

    Args:
        file_path: Absolute path to the file to upload
        query: Optional question about the file (triggers LLM analysis)
    """
    path = Path(file_path)
    if not path.exists():
        return json.dumps({"error": f"File not found: {file_path}"})

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        with open(path, "rb") as f:
            files = {"file": (path.name, f)}
            data = {}
            if query:
                data["query"] = query
            r = await client.post(f"{BASE}/api/files/upload", files=files, data=data)
            r.raise_for_status()
            return json.dumps(r.json())


@mcp.tool()
async def analyze_file(filename: str, query: str) -> str:
    """Analyze a previously uploaded file with a question.

    Args:
        filename: Name of the previously uploaded file
        query: Question about the file content
    """
    return json.dumps(await _post("/api/files/analyze", {"filename": filename, "query": query}))


# ─── Images ───

@mcp.tool()
async def generate_image(prompt: str, negative_prompt: str = "",
                         width: int = 1024, height: int = 1024, steps: int = 20) -> str:
    """Generate an image via ComfyUI pipeline.

    Args:
        prompt: Image description prompt
        negative_prompt: Things to avoid in the image
        width: Image width in pixels
        height: Image height in pixels
        steps: Generation steps (higher = more quality, slower)
    """
    return json.dumps(await _post("/api/images/generate", {
        "prompt": prompt, "negative_prompt": negative_prompt,
        "width": width, "height": height, "steps": steps,
    }))


# ─── Spaces ───

@mcp.tool()
async def create_space(name: str, description: str = "", color: str = "#3b82f6") -> str:
    """Create a new space for organizing conversations.

    Args:
        name: Space name
        description: Space description
        color: Hex color code (e.g. #3b82f6)
    """
    return json.dumps(await _post("/api/spaces", {"name": name, "description": description, "color": color}))


@mcp.tool()
async def list_spaces() -> str:
    """List all spaces with conversation counts."""
    return json.dumps(await _get("/api/spaces"))


@mcp.tool()
async def get_space(space_id: int) -> str:
    """Get a space with its conversations.

    Args:
        space_id: The space ID
    """
    return json.dumps(await _get(f"/api/spaces/{space_id}"))


@mcp.tool()
async def update_space(space_id: int, name: str | None = None,
                       description: str | None = None, color: str | None = None) -> str:
    """Update a space's properties.

    Args:
        space_id: The space ID
        name: New name (optional)
        description: New description (optional)
        color: New hex color (optional)
    """
    data = {}
    if name is not None:
        data["name"] = name
    if description is not None:
        data["description"] = description
    if color is not None:
        data["color"] = color
    return json.dumps(await _patch(f"/api/spaces/{space_id}", data))


@mcp.tool()
async def delete_space(space_id: int) -> str:
    """Delete a space (conversations are unlinked, not deleted).

    Args:
        space_id: The space ID to delete
    """
    return json.dumps(await _delete(f"/api/spaces/{space_id}"))


@mcp.tool()
async def add_to_space(space_id: int, conversation_id: int) -> str:
    """Add a conversation to a space.

    Args:
        space_id: Target space ID
        conversation_id: Conversation to add
    """
    return json.dumps(await _post(f"/api/spaces/{space_id}/add/{conversation_id}"))


# ─── Export ───

@mcp.tool()
async def export_markdown(conversation_id: int) -> str:
    """Export a conversation as Markdown. Returns the markdown text.

    Args:
        conversation_id: The conversation to export
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{BASE}/api/export/markdown/{conversation_id}")
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "json" in ct:
            return json.dumps(r.json())
        return r.text


@mcp.tool()
async def export_pdf(conversation_id: int) -> str:
    """Export a conversation as PDF. Returns base64-encoded PDF.

    Args:
        conversation_id: The conversation to export
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{BASE}/api/export/pdf/{conversation_id}")
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "json" in ct:
            return json.dumps(r.json())
        return base64.b64encode(r.content).decode()


# ─── Startup ───

async def _wait_for_server():
    """Wait for Alphabetty to be ready."""
    for i in range(60):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                r = await client.get(f"{BASE}/openapi.json")
                if r.status_code == 200:
                    logger.info("Alphabetty is ready")
                    return
        except httpx.ConnectError:
            pass
        await asyncio.sleep(0.5)
    logger.warning("Alphabetty may not be ready — proceeding anyway")


if __name__ == "__main__":
    mcp.run()
