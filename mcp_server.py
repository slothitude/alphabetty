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
API_KEY = os.environ.get("ALPHABETTY_API_KEY", "")
BOOTSTRAP_TOKEN = os.environ.get("ALPHABETTY_BOOTSTRAP_TOKEN", "")
SESSION_NAME = os.environ.get("ALPHABETTY_SESSION_NAME", "")
_leased_key: str | None = None  # set if we acquired a session

mcp = FastMCP("alphabetty", instructions="Alphabetty AI research assistant — search, chat, browse, research, knowledge graph, and more.")


# ─── Helpers ───

def _headers():
    key = API_KEY or _leased_key or ""
    return {"Authorization": f"Bearer {key}"} if key else {}


async def _get(path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{BASE}{path}", params=params, headers=_headers())
        r.raise_for_status()
        return r.json()


async def _post(path: str, json_data: dict | None = None, files=None) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(f"{BASE}{path}", json=json_data, files=files, headers=_headers())
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "application/json" in ct:
            return r.json()
        # Binary response (screenshot, PDF, markdown)
        return {"content_type": ct, "data_base64": base64.b64encode(r.content).decode(), "size": len(r.content)}


async def _patch(path: str, json_data: dict) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.patch(f"{BASE}{path}", json=json_data, headers=_headers())
        r.raise_for_status()
        return r.json()


async def _delete(path: str) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.delete(f"{BASE}{path}", headers=_headers())
        r.raise_for_status()
        return r.json()


async def _sse_post(path: str, json_data: dict) -> str:
    """POST to an SSE endpoint, consume the full stream, return aggregated result."""
    tokens = []
    final_event = {}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        async with client.stream("POST", f"{BASE}{path}", json=json_data, headers=_headers()) as resp:
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
async def read_url(url: str, max_length: int = 50000, format: str = "text",
                   include_links: bool = False) -> str:
    """Fetch a URL and return clean extracted text. Fast HTTP fetch with 30-min cache. Use this instead of cdp_navigate+cdp_get_content for reading articles and documentation.

    Args:
        url: URL to fetch and extract text from
        max_length: Maximum text length to return (default 50000)
        format: Output format — "text" (default) or "markdown" (add heading markers)
        include_links: Include extracted links summary (default false)
    """
    return json.dumps(await _get("/api/read-url", {
        "url": url, "max_length": max_length, "format": format,
        "include_links": str(include_links).lower(),
    }))


@mcp.tool()
async def search_and_read(query: str, max_results: int = 3, max_length_per_page: int = 8000) -> str:
    """Search the web and read the top results in one call. Returns search snippets plus full extracted text from each page. Much faster than search then read_url separately.

    Args:
        query: Search query string
        max_results: Number of top results to read (1-5, default 3)
        max_length_per_page: Max text length per page (default 8000)
    """
    return json.dumps(await _post("/api/search-and-read", {
        "query": query, "max_results": max_results, "max_length_per_page": max_length_per_page,
    }))


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
async def cdp_navigate(url: str, tab_id: str = "") -> str:
    """Navigate Chrome to a URL.

    Args:
        url: The URL to navigate to
        tab_id: Target tab ID (optional, defaults to first page tab)
    """
    payload = {"url": url}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/navigate", payload))


@mcp.tool()
async def cdp_get_content(tab_id: str = "") -> str:
    """Get the text content of the current page.

    Args:
        tab_id: Target tab ID (optional, defaults to first page tab)
    """
    params = {}
    if tab_id:
        params["tab_id"] = tab_id
    return json.dumps(await _get("/api/cdp/content", params))


@mcp.tool()
async def cdp_get_dom(depth: int = 3, tab_id: str = "") -> str:
    """Get the DOM structure of the current page.

    Args:
        depth: DOM tree depth to return (1-10)
        tab_id: Target tab ID (optional, defaults to first page tab)
    """
    params = {"depth": depth}
    if tab_id:
        params["tab_id"] = tab_id
    return json.dumps(await _get("/api/cdp/dom", params))


@mcp.tool()
async def cdp_evaluate(expression: str, tab_id: str = "") -> str:
    """Run JavaScript in the browser and return the result.

    Args:
        expression: JavaScript expression to evaluate
        tab_id: Target tab ID (optional, defaults to first page tab)
    """
    payload = {"expression": expression}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/evaluate", payload))


@mcp.tool()
async def cdp_click(selector: str, tab_id: str = "") -> str:
    """Click an element on the page with human-like random offset and timing.

    Args:
        selector: CSS selector for the element to click
        tab_id: Target tab ID (optional, defaults to first page tab)
    """
    payload = {"selector": selector}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/click", payload))


@mcp.tool()
async def cdp_type(selector: str, text: str, tab_id: str = "") -> str:
    """Type text into an element with human-like random delays between keystrokes.

    Args:
        selector: CSS selector for the input element
        text: Text to type
        tab_id: Target tab ID (optional, defaults to first page tab)
    """
    payload = {"selector": selector, "text": text}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/type", payload))


@mcp.tool()
async def cdp_scroll(x: int = 0, y: int = 300, tab_id: str = "") -> str:
    """Scroll the page with human-like behavior.

    Args:
        x: Horizontal scroll pixels
        y: Vertical scroll pixels
        tab_id: Target tab ID (optional, defaults to first page tab)
    """
    payload = {"x": x, "y": y}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/scroll", payload))


@mcp.tool()
async def cdp_screenshot(format: str = "png", tab_id: str = "") -> str:
    """Take a screenshot of the current page. Returns base64-encoded image.

    Args:
        format: Image format - png or jpeg
        tab_id: Target tab ID (optional, defaults to first page tab)
    """
    params = {"format": format}
    if tab_id:
        params["tab_id"] = tab_id
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{BASE}/api/cdp/screenshot", params=params, headers=_headers())
        r.raise_for_status()
        return base64.b64encode(r.content).decode()


@mcp.tool()
async def cdp_extract(expression: str, tab_id: str = "") -> str:
    """Extract data from the current page using a JavaScript expression.

    Args:
        expression: JavaScript expression that returns data to extract
        tab_id: Target tab ID (optional, defaults to first page tab)
    """
    payload = {"expression": expression}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/extract", payload))


@mcp.tool()
async def cdp_query(selector: str, tab_id: str = "") -> str:
    """Query elements on the page using a CSS selector. Returns matching node IDs.

    Args:
        selector: CSS selector to query
        tab_id: Target tab ID (optional, defaults to first page tab)
    """
    payload = {"selector": selector}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/query", payload))


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


# ─── Universal Video Player ───

@mcp.tool()
async def play_video(url: str) -> str:
    """Play any video URL (YouTube, Facebook, X/Twitter, Instagram, TikTok, etc.).
    Uses yt-dlp to extract a direct stream URL.

    Args:
        url: Video URL to play
    """
    return json.dumps(await _post("/api/cdp/video/play", {"url": url}))


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
            r = await client.post(f"{BASE}/api/files/upload", files=files, data=data, headers=_headers())
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
        r = await client.get(f"{BASE}/api/export/markdown/{conversation_id}", headers=_headers())
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
        r = await client.get(f"{BASE}/api/export/pdf/{conversation_id}", headers=_headers())
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "json" in ct:
            return json.dumps(r.json())
        return base64.b64encode(r.content).decode()


# ─── Sign-In ───

@mcp.tool()
async def signin_start(url: str, username: str, password: str, tab_id: str = "") -> str:
    """Start a sign-in flow for a website. Navigates to URL, detects login form, fills credentials, submits. Returns state (waiting_2fa, signed_in, or failed).

    Args:
        url: Login page URL
        username: Email or username
        password: Password
        tab_id: Target tab ID (optional, defaults to first page tab)
    """
    payload = {"url": url, "username": username, "password": password}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/signin/start", payload))


@mcp.tool()
async def signin_submit_2fa(code: str, tab_id: str = "") -> str:
    """Submit a 2FA/verification code during a sign-in flow. Use after signin_start returns waiting_2fa.

    Args:
        code: The 2FA verification code
        tab_id: Target tab ID (optional, defaults to first page tab)
    """
    payload = {"code": code}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/signin/2fa", payload))


@mcp.tool()
async def signin_check_2fa(tab_id: str = "") -> str:
    """Check if the current page is asking for 2FA/verification code.

    Args:
        tab_id: Target tab ID (optional, defaults to first page tab)
    """
    params = {}
    if tab_id:
        params["tab_id"] = tab_id
    return json.dumps(await _get("/api/signin/check-2fa", params))


@mcp.tool()
async def signin_status() -> str:
    """Get the current sign-in workflow state (idle, signing_in, waiting_2fa, signed_in, failed)."""
    return json.dumps(await _get("/api/signin/status"))


@mcp.tool()
async def signin_auto(name: str, tab_id: str = "") -> str:
    """Automatically sign in using a saved credential profile. Handles TOTP auto-generation if configured.

    Args:
        name: Name of the saved credential profile (e.g. 'google', 'github')
        tab_id: Target tab ID (optional, defaults to first page tab)
    """
    url = f"/api/signin/auto/{name}"
    if tab_id:
        url += f"?tab_id={tab_id}"
    return json.dumps(await _post(url))


@mcp.tool()
async def signin_save(name: str, url: str, username: str, password: str,
                      totp_secret: str = "", selectors: str = "") -> str:
    """Save a credential profile for auto sign-in. Stores site URL, username, password, and optional TOTP secret.

    Args:
        name: Profile name (e.g. 'google', 'github')
        url: Login page URL
        username: Email or username
        password: Password
        totp_secret: Optional TOTP secret for auto 2FA
        selectors: Optional JSON string with custom CSS selectors {"username": "...", "password": "...", "submit": "..."}
    """
    payload = {"name": name, "url": url, "username": username, "password": password}
    if totp_secret:
        payload["totp_secret"] = totp_secret
    if selectors:
        try:
            payload["selectors"] = json.loads(selectors)
        except json.JSONDecodeError:
            pass
    return json.dumps(await _post("/api/signin/credentials", payload))


# ─── Workflows (n8n) ───

@mcp.tool()
async def workflow_list() -> str:
    """List all n8n workflows with name, ID, active status, and node count."""
    return json.dumps(await _get("/api/workflows"))


@mcp.tool()
async def workflow_create(name: str, nodes: str = "[]", connections: str = "{}",
                          active: bool = False) -> str:
    """Create a new n8n workflow from JSON nodes and connections. Builds persistent automations — scheduled tasks, webhooks, data pipelines.

    Args:
        name: Workflow name
        nodes: JSON array of n8n node objects (each needs type, name, parameters, position)
        connections: JSON object mapping node names to their connections
        active: Whether to activate the workflow immediately
    """
    import json as _json
    payload = {"name": name, "active": active}
    try:
        payload["nodes"] = _json.loads(nodes)
    except _json.JSONDecodeError:
        return _json.dumps({"error": "Invalid nodes JSON"})
    try:
        payload["connections"] = _json.loads(connections)
    except _json.JSONDecodeError:
        return _json.dumps({"error": "Invalid connections JSON"})
    return json.dumps(await _post("/api/workflows", payload))


@mcp.tool()
async def workflow_run(workflow_id: str, data: str = "{}") -> str:
    """Trigger an n8n workflow execution by ID.

    Args:
        workflow_id: The workflow ID to execute
        data: Optional JSON input data for the workflow
    """
    import json as _json
    try:
        parsed_data = _json.loads(data)
    except _json.JSONDecodeError:
        return _json.dumps({"error": "Invalid data JSON"})
    return json.dumps(await _post(f"/api/workflows/{workflow_id}/run", {"data": parsed_data}))


@mcp.tool()
async def workflow_status(workflow_id: str, limit: int = 10) -> str:
    """Check execution history for an n8n workflow. Returns recent executions with status and timestamps.

    Args:
        workflow_id: The workflow ID to check
        limit: Max executions to return (default 10)
    """
    return json.dumps(await _get(f"/api/workflows/{workflow_id}/executions", {"limit": limit}))


@mcp.tool()
async def workflow_delete(workflow_id: str) -> str:
    """Delete an n8n workflow by ID.

    Args:
        workflow_id: The workflow ID to delete
    """
    return json.dumps(await _delete(f"/api/workflows/{workflow_id}"))


# ─── Fast / Raw CDP ───

@mcp.tool()
async def models_list() -> str:
    """List all available LLM models across providers (Z.ai, OpenRouter, NVIDIA NIM, Ollama).
    Returns model IDs, provider names, context lengths, and tool support status."""
    return json.dumps(await _get("/api/models"))


@mcp.tool()
async def models_refresh() -> str:
    """Force refresh model lists from all providers.
    Discovers new models from OpenRouter, NVIDIA NIM, and Ollama."""
    return json.dumps(await _post("/api/models/refresh"))


@mcp.tool()
async def raw_cdp(method: str, params: str = "{}", tab_id: str = "") -> str:
    """Send any raw CDP protocol command directly to Chrome.

    Args:
        method: CDP method name (e.g. 'Input.dispatchMouseEvent', 'Runtime.evaluate')
        params: JSON string of parameters
        tab_id: Target tab ID (optional)
    """
    payload = {"method": method, "params": json.loads(params)}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/send", payload))


@mcp.tool()
async def insert_text(text: str, tab_id: str = "") -> str:
    """Insert text at cursor natively via CDP Input.insertText.
    Triggers all browser events — works with React/Vue/Angular.
    Much faster than character-by-character type_text.

    Args:
        text: Text to insert
        tab_id: Target tab ID (optional)
    """
    payload = {"text": text}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/insert-text", payload))


@mcp.tool()
async def click_at(x: float, y: float, tab_id: str = "") -> str:
    """Fast mouse click at exact pixel coordinates. No delays.
    Use when you know the x,y position and need speed.

    Args:
        x: X coordinate
        y: Y coordinate
        tab_id: Target tab ID (optional)
    """
    payload = {"x": x, "y": y}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/click-at", payload))


@mcp.tool()
async def click_iframe(selector: str, iframe_selector: str = "iframe", tab_id: str = "") -> str:
    """Click an element inside an iframe. Calculates absolute coordinates from iframe position.

    Args:
        selector: CSS selector for element inside the iframe
        iframe_selector: CSS selector for the iframe element (default: 'iframe')
        tab_id: Target tab ID (optional)
    """
    payload = {"selector": selector, "iframe_selector": iframe_selector}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/click-iframe", payload))


@mcp.tool()
async def type_iframe(selector: str, text: str, iframe_selector: str = "iframe", tab_id: str = "") -> str:
    """Focus element inside an iframe and insert text natively.
    Uses CDP Input.insertText — works with React editors in iframes.

    Args:
        selector: CSS selector for element inside the iframe
        text: Text to type
        iframe_selector: CSS selector for the iframe (default: 'iframe')
        tab_id: Target tab ID (optional)
    """
    payload = {"selector": selector, "text": text, "iframe_selector": iframe_selector}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/type-iframe", payload))


@mcp.tool()
async def upload_file_url(selector: str, file_url: str, tab_id: str = "") -> str:
    """Download a file from URL and upload it to a file input element.
    No base64 needed — downloads directly to temp file and sets on input.

    Args:
        selector: CSS selector for the file input element
        file_url: URL of the file to download and upload
        tab_id: Target tab ID (optional)
    """
    payload = {"selector": selector, "file_url": file_url}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/upload-url", payload))


# ─── Computer Use (screen control for AI agents) ───

_VP = os.environ.get("ALPHABETTY_CHROME_WINDOW_SIZE", "1920,1080")
_VP_W, _VP_H = [int(x) for x in _VP.split(",")]


@mcp.tool()
async def computer_screenshot(tab_id: str = "") -> str:
    """Take a screenshot of the current browser viewport. Returns base64-encoded JPEG image with viewport dimensions.
    Use this to see the current state of the page before taking actions.

    Args:
        tab_id: Target tab ID (optional)
    """
    params = {"format": "jpeg"}
    if tab_id:
        params["tab_id"] = tab_id
    result = await _get("/api/cdp/screenshot", params)
    # _get returns JSON, but screenshot returns binary — use _post path which handles binary
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{BASE}/api/cdp/screenshot", params=params, headers=_headers())
        r.raise_for_status()
        img_b64 = base64.b64encode(r.content).decode()
    return json.dumps({"image": img_b64, "width": _VP_W, "height": _VP_H, "format": "jpeg"})


@mcp.tool()
async def computer_click(x: float, y: float, tab_id: str = "") -> str:
    """Click at exact pixel coordinates on the screen. Use after computer_screenshot to determine where to click.

    Args:
        x: X coordinate (pixels from left)
        y: Y coordinate (pixels from top)
        tab_id: Target tab ID (optional)
    """
    payload = {"x": x, "y": y}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/click-at", payload))


@mcp.tool()
async def computer_type(text: str, tab_id: str = "") -> str:
    """Type text into the currently focused element. Click on an input field first with computer_click, then use this to type.

    Args:
        text: Text to type into the focused element
        tab_id: Target tab ID (optional)
    """
    payload = {"text": text}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/insert-text", payload))


@mcp.tool()
async def computer_scroll(direction: str, amount: int = 300, tab_id: str = "") -> str:
    """Scroll the page up or down by a specified amount in pixels.

    Args:
        direction: Scroll direction — "up" or "down"
        amount: Number of pixels to scroll (default 300)
        tab_id: Target tab ID (optional)
    """
    y = -abs(amount) if direction.lower() == "up" else abs(amount)
    payload = {"x": 0, "y": y}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/scroll", payload))


@mcp.tool()
async def computer_key(key: str, tab_id: str = "") -> str:
    """Press a keyboard key or key combination. Supports modifier combos like 'ctrl+a', 'ctrl+c', 'ctrl+v', 'shift+tab', etc.
    Also supports special keys: Enter, Tab, Escape, Backspace, Delete, ArrowUp, ArrowDown, etc.

    Args:
        key: Key or key combo to press (e.g. 'Enter', 'ctrl+a', 'ctrl+c', 'Escape', 'Tab', 'shift+tab')
        tab_id: Target tab ID (optional)
    """
    payload = {"key": key}
    if tab_id:
        payload["tab_id"] = tab_id
    return json.dumps(await _post("/api/cdp/press-key", payload))


# ─── Download Proxy ───

@mcp.tool()
async def download_proxy(url: str, filename: str = "") -> str:
    """Download a file or image from any URL. Streams it back as a file attachment.
    Returns the download URL and metadata.

    Args:
        url: URL to download
        filename: Optional filename override
    """
    params = {"url": url}
    if filename:
        params["filename"] = filename
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        r = await client.get(f"{BASE}/api/download/proxy", params=params, headers=_headers())
        if r.status_code >= 400:
            try:
                return json.dumps(r.json())
            except Exception:
                return json.dumps({"error": f"HTTP {r.status_code}"})
        ct = r.headers.get("content-type", "")
        cd = r.headers.get("content-disposition", "")
        size = len(r.content)
        fname = filename or "download"
        if "filename=" in cd:
            for part in cd.split(";"):
                part = part.strip()
                if part.startswith("filename="):
                    fname = part.split("=", 1)[1].strip().strip('"').strip("'")
        return json.dumps({
            "filename": fname,
            "content_type": ct,
            "size_bytes": size,
            "download_url": f"{BASE}/api/download/proxy?url={url}",
            "data_base64": base64.b64encode(r.content).decode() if size < 10 * 1024 * 1024 else None,
        })


@mcp.tool()
async def download_save(url: str, filename: str = "", subdir: str = "") -> str:
    """Download a file from URL and save it to the server's download directory.
    Returns the saved file path and metadata. Use this to persist files to disk.

    Args:
        url: URL to download
        filename: Optional filename override
        subdir: Optional subdirectory within the download folder
    """
    payload = {"url": url}
    if filename:
        payload["filename"] = filename
    if subdir:
        payload["subdir"] = subdir
    return json.dumps(await _post("/api/download/save", payload))


@mcp.tool()
async def download_list(subdir: str = "") -> str:
    """List files saved in the server's download directory.

    Args:
        subdir: Optional subdirectory to list
    """
    params = {}
    if subdir:
        params["subdir"] = subdir
    return json.dumps(await _get("/api/download/files", params))


# ─── Swarm ───

@mcp.tool()
async def swarm_status() -> str:
    """Get swarm status: this instance's capabilities and peer health.
    Shows which Alphabetty instances are available and what they can do."""
    return json.dumps(await _get("/api/swarm/status"))


@mcp.tool()
async def swarm_execute(peer_url: str, tool: str, args: str = "{}") -> str:
    """Execute a tool on a remote swarm peer. Routes through the swarm transport layer.
    Use swarm_status first to find available peers and their capabilities.

    Args:
        peer_url: Base URL of the target peer (e.g. 'http://192.168.0.33:7700')
        tool: Tool name to execute (e.g. 'browse', 'click', 'screenshot')
        args: JSON string of tool arguments
    """
    import json as _json
    try:
        parsed_args = _json.loads(args)
    except _json.JSONDecodeError:
        return _json.dumps({"error": "Invalid args JSON"})

    swarm_key = os.environ.get("ALPHABETTY_SWARM_KEY", "")
    url = f"{peer_url.rstrip('/')}/api/v1/swarm/execute"
    payload = {"tool": tool, "args": parsed_args}
    headers = {"Content-Type": "application/json"}
    if swarm_key:
        headers["X-Swarm-Key"] = swarm_key

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        r = await client.post(url, json=payload, headers=headers)
        if r.status_code >= 400:
            return json.dumps({"error": f"HTTP {r.status_code}", "detail": r.text[:500]})
        return json.dumps(r.json())


# ─── Extension (control user's browser tab via Chrome extension) ───

async def ext_status() -> str:
    """Check if the user's Chrome extension is connected. Returns connection state.

    The extension must be installed and have the sidebar open for commands to work.
    If disconnected, the user needs to open the Alphabetty page and click the extension icon.
    """
    return json.dumps(await _get("/api/v1/ext/health"))


async def ext_execute(action: str, params: str = "{}") -> str:
    """Execute a command on the user's browser tab via the Chrome extension.

    Requires the extension to be connected (sidebar open on an Alphabetty page).
    This controls the user's ACTUAL browser — their cookies, login state, and session.
    Unlike CDP (headless), the extension sees what the user sees.

    Args:
        action: One of: evaluate, click, type, navigate, getDOM, getText
            - evaluate: Run JavaScript expression in page context. Returns the result.
            - click: Click an element by CSS selector.
            - type: Type text into an element (handles contenteditable).
            - navigate: Navigate to a URL.
            - getDOM: Get page DOM structure (depth param, default 3).
            - getText: Get visible text content of the page.
        params: JSON object with action-specific parameters:
            - evaluate: {"expression": "document.title"}
            - click: {"selector": "#my-button"}
            - type: {"selector": "#input", "text": "hello"}
            - navigate: {"url": "https://example.com"}
            - getDOM: {"depth": 3}
            - getText: {}
    """
    try:
        p = json.loads(params)
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON in params"})
    return json.dumps(await _post("/api/v1/ext/execute", {"action": action, "params": p}))


# ─── Session Leasing ───

async def _acquire_session():
    """Acquire an ephemeral session via bootstrap token. Sets _leased_key."""
    global _leased_key
    if not BOOTSTRAP_TOKEN or API_KEY:
        return  # static key or no bootstrap — skip
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            r = await client.post(
                f"{BASE}/api/auth/session/acquire",
                json={"name": SESSION_NAME},
                headers={"X-Bootstrap-Token": BOOTSTRAP_TOKEN},
            )
            r.raise_for_status()
            data = r.json()
            _leased_key = data["api_key"]
            logger.info(f"Acquired session: {data.get('username', '?')}")
    except Exception as e:
        logger.warning(f"Session acquire failed: {e}")


async def _release_session():
    """Release the leased session."""
    if not _leased_key:
        return
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            await client.post(
                f"{BASE}/api/auth/session/release",
                headers={"Authorization": f"Bearer {_leased_key}"},
            )
            logger.info("Released session")
    except Exception as e:
        logger.warning(f"Session release failed: {e}")


# ─── Startup ───

# ─── Self-Healing Doctor ───

@mcp.tool()
async def doctor_check() -> str:
    """Check health of all Lappy services Alphabetty depends on."""
    from core.doctor import get_health_status, get_fix_log
    return json.dumps({"health": get_health_status(), "recent_fixes": get_fix_log()[-10:]})


@mcp.tool()
async def doctor_call(symptom: str, service: str = "") -> str:
    """Call Dr. Claude Code to diagnose and fix a failing Lappy service.

    Args:
        symptom: What's broken. Include error messages.
        service: Service name: searxng, ollama, litellm
    """
    from core.doctor import call_doctor
    result = await call_doctor(symptom=symptom, service=service)
    return json.dumps(result)


async def _wait_for_server():
    """Wait for Alphabetty to be ready."""
    for i in range(60):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                r = await client.get(f"{BASE}/openapi.json", headers=_headers())
                if r.status_code == 200:
                    logger.info("Alphabetty is ready")
                    return
        except httpx.ConnectError:
            pass
        await asyncio.sleep(0.5)
    logger.warning("Alphabetty may not be ready — proceeding anyway")


if __name__ == "__main__":
    # Acquire session before running (if bootstrap token set)
    if BOOTSTRAP_TOKEN and not API_KEY:
        asyncio.run(_wait_for_server())
        asyncio.run(_acquire_session())
    try:
        mcp.run()
    finally:
        if _leased_key:
            asyncio.run(_release_session())
