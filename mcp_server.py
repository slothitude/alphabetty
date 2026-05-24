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
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx
from fastmcp import FastMCP

try:
    import paramiko
    _HAS_PARAMIKO = True
except ImportError:
    _HAS_PARAMIKO = False

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


# ─── Media Stack (SSH to Lappy) ───

# Configuration
_MEDIA_SSH_HOST = "192.168.0.33"
_MEDIA_SSH_USER = "aaron"
_MEDIA_SSH_PASS = "T0b1@n7243"

_MEDIA_SERVICES = {
    "radarr": {"port": 7878, "key": "8e1f87572aff4f5f8d1b3ecf0d07de0f"},
    "sonarr": {"port": 8989, "key": "0b7a42764d754cf98b88cd04c9ab24a8"},
    "lidarr": {"port": 8686, "key": "61e90ca7a70c4e43bfee9ed9fe0831cd"},
    "prowlarr": {"port": 9696, "key": "4e3133c43d2946ce8221b139daf81898"},
}
_MEDIA_QB_USER = "admin"
_MEDIA_QB_PASS = "adminadmin"
_MEDIA_JF_USER = "admin"
_MEDIA_JF_PASS = "Tobiano01"
_MEDIA_JELLYSEERR_KEY = "MTc3OTQ5NTU3NzY0NWVkMTQwZmVmLTZkOTEtNDY3Ni04YTMwLTVjNTUzMGRiZWZhOA=="
_MEDIA_TAILSCALE_IP = "100.84.161.63"

_media_ssh_client = None


def _media_get_ssh() -> "paramiko.SSHClient":
    if not _HAS_PARAMIKO:
        raise RuntimeError("paramiko not installed — media tools unavailable in this environment")
    global _media_ssh_client
    if _media_ssh_client is None or _media_ssh_client.get_transport() is None or not _media_ssh_client.get_transport().is_active():
        _media_ssh_client = paramiko.SSHClient()
        _media_ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        _media_ssh_client.connect(_MEDIA_SSH_HOST, username=_MEDIA_SSH_USER, password=_MEDIA_SSH_PASS, timeout=10)
    return _media_ssh_client


def _media_ssh_exec(cmd: str) -> str:
    ssh = _media_get_ssh()
    _, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return out if out.strip() else err


def _media_ssh_python(code: str) -> str:
    write_cmd = (
        'python -X utf8 -c "'
        'import urllib.request,json,sys,http.cookiejar;'
        + code.replace('"', '\\"')
        + '"'
    )
    return _media_ssh_exec(write_cmd)


def _media_http_get(url: str, headers: dict = None) -> dict:
    h = headers or {}
    script = (
        f"req=urllib.request.Request('{url}',headers={h});"
        f"resp=urllib.request.urlopen(req,timeout=15);"
        f"sys.stdout.write(resp.read().decode())"
    )
    raw = _media_ssh_python(script)
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": f"non-JSON: {raw[:300]}"}


def _media_http_post(url: str, data: dict = None, headers: dict = None, method: str = "POST") -> dict:
    h = headers or {}
    body = json.dumps(data).encode() if data else b""
    h.setdefault("Content-Type", "application/json")
    b64_body = base64.b64encode(body).decode() if body else ""
    script = (
        f"import base64;"
        f"body=base64.b64decode('{b64_body}') if '{b64_body}' else None;"
        f"req=urllib.request.Request('{url}',data=body,headers={h},method='{method}');"
        f"resp=urllib.request.urlopen(req,timeout=15);"
        f"sys.stdout.write(resp.read().decode())"
    )
    raw = _media_ssh_python(script)
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": f"non-JSON: {raw[:300]}"}


def _media_arr_api(service: str, path: str, method: str = "GET", data: dict = None) -> dict:
    port = _MEDIA_SERVICES[service]["port"]
    key = _MEDIA_SERVICES[service]["key"]
    url = f"http://localhost:{port}/api/v3{path}?apiKey={key}"
    if method == "GET":
        return _media_http_get(url)
    return _media_http_post(url, data, method=method)


# Jellyseerr
def _media_js_api(path: str, method: str = "GET", data: dict = None) -> dict:
    url = f"http://localhost:5055/api/v1{path}"
    headers = {"X-Api-Key": _MEDIA_JELLYSEERR_KEY}
    if method == "GET":
        return _media_http_get(url, headers)
    return _media_http_post(url, data, headers, method=method)


# qBittorrent auth
_media_qb_sid = None


def _media_qb_login() -> str:
    global _media_qb_sid
    if _media_qb_sid:
        return _media_qb_sid
    form = f"username={_MEDIA_QB_USER}&password={_MEDIA_QB_PASS}".encode()
    b64 = base64.b64encode(form).decode()
    script = (
        f"import base64,http.cookiejar,urllib.request;"
        f"body=base64.b64decode('{b64}');"
        f"cj=http.cookiejar.CookieJar();"
        f"opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj));"
        f"req=urllib.request.Request('http://localhost:8090/api/v2/auth/login',"
        f"data=body,headers={{'Content-Type':'application/x-www-form-urlencoded'}});"
        f"resp=opener.open(req,timeout=15);"
        f"cookies=[c for c in cj if 'SID' in c.name];"
        f"sys.stdout.write(cookies[0].name+'='+cookies[0].value if cookies else 'NO_SID')"
    )
    result = _media_ssh_python(script).strip()
    if result and result != "NO_SID":
        _media_qb_sid = result
    return _media_qb_sid or ""


def _media_qb_api(path: str, method: str = "GET", data: dict = None) -> dict:
    sid = _media_qb_login()
    url = f"http://localhost:8090/api/v2{path}"
    if method == "GET":
        return _media_http_get(url, headers={"Cookie": sid})
    form = urllib.parse.urlencode(data or {}).encode()
    b64 = base64.b64encode(form).decode()
    script = (
        f"import base64,urllib.request;"
        f"body=base64.b64decode('{b64}');"
        f"req=urllib.request.Request('{url}',data=body,"
        f"headers={{'Content-Type':'application/x-www-form-urlencoded','Cookie':'{sid}'}});"
        f"resp=urllib.request.urlopen(req,timeout=15);"
        f"sys.stdout.write(resp.read().decode())"
    )
    raw = _media_ssh_python(script)
    if not raw.strip():
        return {"status": "ok"}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw[:500]}


# Jellyfin auth
_media_jf_token = None
_media_jf_user = None

_JF_LAN = f"http://{_MEDIA_SSH_HOST}:8096"
_JF_TS = f"http://{_MEDIA_TAILSCALE_IP}:8096"


def _media_jf_auth() -> tuple:
    global _media_jf_token, _media_jf_user
    body = json.dumps({"Username": _MEDIA_JF_USER, "Pw": _MEDIA_JF_PASS}).encode()
    b64 = base64.b64encode(body).decode()
    script = (
        "import base64,json,urllib.request;"
        f"body=base64.b64decode('{b64}');"
        "req=urllib.request.Request('http://localhost:8096/Users/AuthenticateByName',"
        "data=body,headers={'Content-Type':'application/json',"
        "'X-Emby-Authorization':'MediaBrowser Client=mcptool, Device=cli, DeviceId=mcptool, Version=1.0'});"
        "resp=urllib.request.urlopen(req,timeout=15);"
        "sys.stdout.write(resp.read().decode())"
    )
    raw = _media_ssh_python(script)
    auth = json.loads(raw)
    return auth["AccessToken"], auth["User"]["Id"]


def _media_jf_search(query: str, item_types: str = "Movie,Series") -> list:
    global _media_jf_token, _media_jf_user
    if not _media_jf_token:
        _media_jf_token, _media_jf_user = _media_jf_auth()
    q = urllib.parse.quote(query)
    url = (
        f"http://localhost:8096/Items?SearchTerm={q}"
        f"&Recursive=true&IncludeItemTypes={item_types}"
        f"&Limit=10&api_key={_media_jf_token}"
    )
    result = _media_http_get(url)
    return result.get("Items", [])


def _media_stream_urls(item_id: str) -> dict:
    params = f"/Videos/{item_id}/stream.mp4?mediaSourceId={item_id}&api_key={_media_jf_token}&AudioCodec=aac&AudioBitRate=128000"
    return {"lan": f"{_JF_LAN}{params}", "tailscale": f"{_JF_TS}{params}"}


# ── Media Stack MCP Tools ──

@mcp.tool()
async def media_search(query: str) -> str:
    """Search Jellyfin library for movies/TV shows. Returns direct-play stream links (no login required).
    URLs provided for both LAN and Tailscale (remote access).
    For series, returns individual episodes with play links.

    Args:
        query: Search term (e.g. "The Matrix")
    """
    try:
        items = _media_jf_search(query)
        if not items:
            return json.dumps({"message": f"No results in Jellyfin for '{query}'. Use media_request to add it."})
        results = []
        for item in items[:5]:
            item_id = item["Id"]
            item_type = item.get("Type", "")
            if item_type == "Series":
                eps_url = (
                    f"http://localhost:8096/Shows/{item_id}/Episodes?"
                    f"UserId={_media_jf_user}&Fields=MediaSources&api_key={_media_jf_token}"
                )
                eps_data = _media_http_get(eps_url)
                episodes = eps_data.get("Items", [])
                for ep in episodes[:10]:
                    ep_id = ep["Id"]
                    urls = _media_stream_urls(ep_id)
                    snum = ep.get("ParentIndexNumber", "?")
                    enum = ep.get("IndexNumber", "?")
                    results.append({
                        "title": f"{item.get('Name')} S{snum:02d}E{enum:02d} - {ep.get('Name', '')}",
                        "year": item.get("ProductionYear"),
                        "type": "Episode",
                        "play_lan": urls["lan"],
                        "play_remote": urls["tailscale"],
                        "details": f"{_JF_TS}/web/#/details?id={ep_id}",
                    })
            else:
                urls = _media_stream_urls(item_id)
                results.append({
                    "title": item.get("Name"),
                    "year": item.get("ProductionYear"),
                    "type": item_type,
                    "play_lan": urls["lan"],
                    "play_remote": urls["tailscale"],
                    "details": f"{_JF_TS}/web/#/details?id={item_id}",
                })
        return json.dumps(results, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def search_tmdb(query: str, media_type: str = "movie") -> str:
    """Search TMDb via Jellyseerr for movies or TV shows to request.
    Returns TMDb IDs, titles, years, and overviews. Use media_request with the ID to download.

    Args:
        query: Search term (e.g. "The Matrix")
        media_type: "movie" or "tv"
    """
    try:
        q = urllib.parse.quote(query)
        result = _media_js_api(f"/search?query={q}")
        if isinstance(result, dict) and "error" in result:
            return json.dumps(result)
        items = result.get("results", []) if isinstance(result, dict) else result
        filtered = [r for r in items if r.get("mediaType") == media_type][:10]
        if not filtered:
            return json.dumps({"message": f"No {media_type} results for '{query}'"})
        out = []
        for r in filtered:
            poster = r.get("posterPath")
            mt = r.get("mediaType", "movie")
            poster_urls = {}
            if poster:
                poster_urls = {
                    "poster_lan": f"http://{_MEDIA_SSH_HOST}:5055/image/{mt}?path={poster}",
                    "poster_tailscale": f"http://{_MEDIA_TAILSCALE_IP}:5055/image/{mt}?path={poster}",
                }
            out.append({
                "tmdb_id": r.get("id"),
                "title": r.get("title") or r.get("name"),
                "year": (r.get("releaseDate") or r.get("firstAirDate") or "")[:4],
                "overview": (r.get("overview") or "")[:150],
                **poster_urls,
            })
        return json.dumps(out, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def media_request(media_id: int, media_type: str = "movie") -> str:
    """Request a movie or TV show via Jellyseerr.

    Args:
        media_id: TMDb ID from search_tmdb results
        media_type: "movie" or "tv"
    """
    try:
        body = {"mediaType": media_type, "mediaId": media_id}
        if media_type == "tv":
            body["seasons"] = "all"
        result = _media_js_api("/request", method="POST", data=body)
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def media_requests(status: Optional[str] = None) -> str:
    """List Jellyseerr requests.

    Args:
        status: Filter - "pending", "approved", "available", "all", or None for default
    """
    try:
        path = "/request"
        if status and status != "all":
            status_map = {"pending": 1, "approved": 2, "available": 3}
            path += f"?filter={status_map.get(status, status)}"
        result = _media_js_api(path)
        if isinstance(result, dict) and "error" in result:
            return json.dumps(result)
        items = result if isinstance(result, list) else result.get("results", [])
        out = []
        for r in (items[:20] if isinstance(items, list) else []):
            media = r.get("media", {})
            out.append({
                "id": r.get("id"),
                "type": r.get("type"),
                "title": media.get("title") or media.get("externalServiceSlug", ""),
                "status": r.get("status"),
                "created": r.get("createdAt", "")[:10],
            })
        return json.dumps(out, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def radarr_movies(status: Optional[str] = None) -> str:
    """List movies in Radarr library.

    Args:
        status: Optional filter - "downloaded", "missing", "monitored"
    """
    try:
        result = _media_arr_api("radarr", "/movie")
        if isinstance(result, dict) and "error" in result:
            return json.dumps(result)
        movies = result if isinstance(result, list) else []
        if status == "downloaded":
            movies = [m for m in movies if m.get("hasFile")]
        elif status == "missing":
            movies = [m for m in movies if not m.get("hasFile") and m.get("monitored")]
        elif status == "monitored":
            movies = [m for m in movies if m.get("monitored")]
        out = []
        for m in movies[:50]:
            out.append({
                "title": m.get("title"),
                "year": m.get("year"),
                "status": m.get("status"),
                "monitored": m.get("monitored"),
                "hasFile": m.get("hasFile"),
                "size_gb": round(m.get("sizeOnDisk", 0) / 1e9, 1) if m.get("sizeOnDisk") else 0,
            })
        return json.dumps(out, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def radarr_queue() -> str:
    """Check Radarr download queue."""
    try:
        result = _media_arr_api("radarr", "/queue")
        if isinstance(result, dict) and "error" in result:
            return json.dumps(result)
        records = result.get("records", [])
        out = []
        for r in records[:20]:
            out.append({
                "title": r.get("title"),
                "status": r.get("status"),
                "progress": round((1 - r.get("sizeleft", 0) / max(r.get("size", 1), 1)) * 100, 1) if r.get("size") else 0,
                "timeleft": r.get("timeleft"),
                "downloadClient": r.get("downloadClient"),
            })
        return json.dumps(out, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def sonarr_series(status: Optional[str] = None) -> str:
    """List series in Sonarr library.

    Args:
        status: Optional filter - "downloaded", "missing", "continuing", "ended"
    """
    try:
        result = _media_arr_api("sonarr", "/series")
        if isinstance(result, dict) and "error" in result:
            return json.dumps(result)
        series = result if isinstance(result, list) else []
        if status == "downloaded":
            series = [s for s in series if s.get("statistics", {}).get("episodeFileCount", 0) > 0]
        elif status == "missing":
            series = [s for s in series if s.get("monitored") and (lambda st: st.get("episodeCount", 0) > st.get("episodeFileCount", 0))(s.get("statistics", {}))]
        elif status in ("continuing", "ended"):
            series = [s for s in series if s.get("status") == status.capitalize()]
        out = []
        for s in series[:50]:
            stats = s.get("statistics", {})
            out.append({
                "title": s.get("title"),
                "status": s.get("status"),
                "monitored": s.get("monitored"),
                "seasons": stats.get("seasonCount", 0),
                "episodes": stats.get("episodeCount", 0),
                "downloaded": stats.get("episodeFileCount", 0),
                "size_gb": round(stats.get("sizeOnDisk", 0) / 1e9, 1) if stats.get("sizeOnDisk") else 0,
            })
        return json.dumps(out, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def sonarr_queue() -> str:
    """Check Sonarr download queue."""
    try:
        result = _media_arr_api("sonarr", "/queue")
        if isinstance(result, dict) and "error" in result:
            return json.dumps(result)
        records = result.get("records", [])
        out = []
        for r in records[:20]:
            out.append({
                "title": r.get("title"),
                "status": r.get("status"),
                "progress": round((1 - r.get("sizeleft", 0) / max(r.get("size", 1), 1)) * 100, 1) if r.get("size") else 0,
                "timeleft": r.get("timeleft"),
                "downloadClient": r.get("downloadClient"),
            })
        return json.dumps(out, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def torrents_list(filter_status: Optional[str] = None) -> str:
    """List torrents from qBittorrent with status/progress/speed.

    Args:
        filter_status: Optional - "all", "downloading", "completed", "paused", "active", "error"
    """
    try:
        path = "/torrents/info"
        if filter_status:
            path += f"?filter={filter_status}"
        result = _media_qb_api(path)
        torrents = result if isinstance(result, list) else []
        out = []
        for t in torrents[:100]:
            out.append({
                "name": t.get("name"),
                "state": t.get("state"),
                "progress": round(t.get("progress", 0) * 100, 1),
                "size_gb": round(t.get("size", 0) / 1e9, 2),
                "dlspeed_mbps": round(t.get("dlspeed", 0) / 1e6, 1),
                "upspeed_mbps": round(t.get("upspeed", 0) / 1e6, 1),
                "eta": t.get("eta"),
                "hash": t.get("hash"),
                "save_path": t.get("save_path"),
                "category": t.get("category"),
            })
        return json.dumps(out[:100], indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def torrents_action(hash: str, action: str = "pause") -> str:
    """Perform an action on a torrent in qBittorrent.

    Args:
        hash: Torrent hash
        action: "pause", "resume", "delete", "delete_files", or "bump" (move to top of queue)
    """
    try:
        if action == "pause":
            result = _media_qb_api("/torrents/pause", method="POST", data={"hashes": hash})
        elif action == "resume":
            result = _media_qb_api("/torrents/resume", method="POST", data={"hashes": hash})
        elif action == "delete":
            result = _media_qb_api("/torrents/delete", method="POST", data={"hashes": hash, "deleteFiles": "false"})
        elif action == "delete_files":
            result = _media_qb_api("/torrents/delete", method="POST", data={"hashes": hash, "deleteFiles": "true"})
        elif action == "bump":
            result = _media_qb_api("/torrents/topPrio", method="POST", data={"hashes": hash})
        else:
            return json.dumps({"error": f"unknown action: {action}"})
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def stack_status() -> str:
    """Docker ps for all media stack containers (health, uptime)."""
    try:
        raw = _media_ssh_exec(
            "docker ps -a --filter \"label=com.docker.compose.project=media-stack\" "
            "--format \"{{.Names}}\t{{.Status}}\t{{.Ports}}\""
        )
        containers = []
        for line in raw.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            containers.append({
                "name": parts[0].strip() if len(parts) > 0 else "",
                "status": parts[1].strip() if len(parts) > 1 else "",
                "ports": parts[2].strip() if len(parts) > 2 else "",
            })
        if not containers:
            raw = _media_ssh_exec("docker ps -a --format \"{{.Names}}\t{{.Status}}\t{{.Ports}}\"")
            media_names = ["gluetun", "qbittorrent", "radarr", "sonarr", "lidarr",
                           "prowlarr", "jellyfin", "jellyseerr", "bazarr", "autobrr",
                           "searxng", "chrome", "qdrant", "rabbit", "guide"]
            for line in raw.strip().splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                name = parts[0].strip() if len(parts) > 0 else ""
                if any(n in name.lower() for n in media_names):
                    containers.append({
                        "name": name,
                        "status": parts[1].strip() if len(parts) > 1 else "",
                        "ports": parts[2].strip() if len(parts) > 2 else "",
                    })
        return json.dumps(containers, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def vpn_status() -> str:
    """Check Gluetun VPN connection (IP, location)."""
    try:
        raw = _media_ssh_exec("docker logs gluetun --tail 30")
        lines = []
        for line in raw.splitlines():
            line = line.strip()
            if any(kw in line.lower() for kw in ["ip ", "location", "country", "city", "vpn", "connected", "public"]):
                lines.append(line)
        ip_info = _media_ssh_exec("docker exec gluetun wget -qO- http://localhost:8000/v1/openvpn/status")
        return json.dumps({
            "log_lines": lines[-10:],
            "status_api": ip_info.strip() if ip_info.strip() else "not available",
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def media_control(action: str, query: Optional[str] = None, hash: Optional[str] = None, service: Optional[str] = None) -> str:
    """One-stop media stack control. Search, browse, manage downloads, check status.

    Args:
        action: What to do:
            - "search": Search Jellyfin for a movie/show. Returns direct-play links (no login).
            - "downloads": List active torrents with progress/speed.
            - "movies": List Radarr movies. Use query to filter by title.
            - "shows": List Sonarr series. Use query to filter by title.
            - "requests": List Jellyseerr pending requests.
            - "status": Show all containers + VPN + disk space.
            - "pause": Pause a torrent (requires hash).
            - "resume": Resume a torrent (requires hash).
            - "bump": Move a torrent to top of download queue (requires hash).
            - "restart": Restart a Docker container (requires service name, e.g. "radarr", "gluetun").
        query: Search/filter term for search, movies, shows actions.
        hash: Torrent hash for pause/resume actions.
        service: Container name for restart action.
    """
    try:
        if action == "search":
            if not query:
                return json.dumps({"error": "query is required for search"})
            items = _media_jf_search(query)
            if not items:
                return json.dumps({"message": f"Nothing in Jellyfin for '{query}'. Try media_request to add it."})
            results = []
            for item in items[:10]:
                item_id = item["Id"]
                urls = _media_stream_urls(item_id)
                results.append({
                    "title": item.get("Name"),
                    "year": item.get("ProductionYear"),
                    "type": item.get("Type"),
                    "play_lan": urls["lan"],
                    "play_remote": urls["tailscale"],
                    "details": f"{_JF_TS}/web/#/details?id={item_id}",
                })
            return json.dumps(results, indent=2)

        elif action == "downloads":
            result = _media_qb_api("/torrents/info")
            torrents = result if isinstance(result, list) else []
            out = []
            for t in torrents[:20]:
                state = t.get("state", "?")
                prog = round(t.get("progress", 0) * 100, 1)
                dl = round(t.get("dlspeed", 0) / 1048576, 1)
                up = round(t.get("upspeed", 0) / 1048576, 1)
                size = round(t.get("size", 0) / 1073741824, 2)
                eta_s = t.get("eta", 0)
                if eta_s and eta_s < 8640000:
                    m, s = divmod(eta_s, 60)
                    h, m = divmod(m, 60)
                    eta_str = f"{h}h{m}m" if h else f"{m}m{s}s"
                else:
                    eta_str = "-"
                out.append({
                    "name": t.get("name"),
                    "state": state,
                    "progress": f"{prog}%",
                    "size_gb": size,
                    "dl_mbps": dl,
                    "up_mbps": up,
                    "eta": eta_str,
                    "hash": t.get("hash"),
                })
            return json.dumps(out, indent=2)

        elif action == "movies":
            result = _media_arr_api("radarr", "/movie")
            movies = result if isinstance(result, list) else []
            if query:
                q = query.lower()
                movies = [m for m in movies if q in m.get("title", "").lower()]
            out = []
            for m in movies[:30]:
                status_icon = "+" if m.get("hasFile") else "-" if m.get("monitored") else "x"
                out.append({
                    "title": f"[{status_icon}] {m.get('title')} ({m.get('year','?')})",
                    "has_file": m.get("hasFile"),
                    "monitored": m.get("monitored"),
                    "status": m.get("status"),
                })
            return json.dumps(out, indent=2)

        elif action == "shows":
            result = _media_arr_api("sonarr", "/series")
            series = result if isinstance(result, list) else []
            if query:
                q = query.lower()
                series = [s for s in series if q in s.get("title", "").lower()]
            out = []
            for s in series[:30]:
                stats = s.get("statistics", {})
                ep = stats.get("episodeCount", 0)
                got = stats.get("episodeFileCount", 0)
                out.append({
                    "title": s.get("title"),
                    "status": s.get("status"),
                    "episodes": f"{got}/{ep}",
                    "monitored": s.get("monitored"),
                })
            return json.dumps(out, indent=2)

        elif action == "requests":
            result = _media_js_api("/request?filter=1")
            items = result.get("results", []) if isinstance(result, dict) else (result if isinstance(result, list) else [])
            out = []
            for r in items[:20]:
                media = r.get("media", {})
                out.append({
                    "title": media.get("title") or media.get("externalServiceSlug", "?"),
                    "type": r.get("type"),
                    "status": r.get("status"),
                    "requested_by": r.get("requestedBy", {}).get("displayName", "?"),
                })
            return json.dumps(out, indent=2)

        elif action == "status":
            raw = _media_ssh_exec(
                "docker ps -a --filter \"label=com.docker.compose.project=media-stack\" "
                "--format \"{{.Names}}\t{{.Status}}\""
            )
            containers = []
            for line in raw.strip().splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    containers.append({"name": parts[0], "status": parts[1]})
            vpn_ip = ""
            try:
                ip_raw = _media_ssh_exec("docker exec gluetun wget -qO- --timeout=5 https://ipinfo.io/json 2>/dev/null")
                ip_data = json.loads(ip_raw)
                vpn_ip = f"{ip_data.get('ip','?')} ({ip_data.get('city','?')}, {ip_data.get('country','?')})"
            except:
                vpn_ip = "unknown"
            disk = _media_ssh_exec("powershell -c \"(Get-PSDrive D).Free / 1GB\"").strip()
            disk_gb = round(float(disk), 0) if disk else "?"
            disk_total = _media_ssh_exec("powershell -c \"(Get-PSDrive D).Used / 1GB + (Get-PSDrive D).Free / 1GB\"").strip()
            disk_total_gb = round(float(disk_total), 0) if disk_total else "?"
            qresult = _media_qb_api("/torrents/info")
            torrents = qresult if isinstance(qresult, list) else []
            downloading = [t for t in torrents if "download" in t.get("state", "").lower()]
            seeding = [t for t in torrents if "upload" in t.get("state", "").lower()]
            return json.dumps({
                "containers": len(containers),
                "container_list": [f"{c['name']}: {c['status']}" for c in containers],
                "vpn": vpn_ip,
                "disk_free_gb": disk_gb,
                "disk_total_gb": disk_total_gb,
                "torrents_downloading": len(downloading),
                "torrents_seeding": len(seeding),
            }, indent=2)

        elif action == "pause":
            if not hash:
                return json.dumps({"error": "hash is required for pause"})
            _media_qb_api("/torrents/pause", method="POST", data={"hashes": hash})
            return json.dumps({"status": "paused", "hash": hash})

        elif action == "resume":
            if not hash:
                return json.dumps({"error": "hash is required for resume"})
            _media_qb_api("/torrents/resume", method="POST", data={"hashes": hash})
            return json.dumps({"status": "resumed", "hash": hash})

        elif action == "bump":
            if not hash:
                return json.dumps({"error": "hash is required for bump"})
            _media_qb_api("/torrents/topPrio", method="POST", data={"hashes": hash})
            return json.dumps({"status": "bumped to top", "hash": hash})

        elif action == "restart":
            if not service:
                return json.dumps({"error": "service name required (e.g. radarr, gluetun, jellyfin)"})
            result = _media_ssh_exec(f"docker restart {service}")
            return json.dumps({"status": "restarted", "service": service, "output": result.strip()})

        else:
            return json.dumps({"error": f"unknown action '{action}'. Use: search, downloads, movies, shows, requests, status, pause, resume, bump, restart"})

    except Exception as e:
        return json.dumps({"error": str(e)})


# ─── Startup ───

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
