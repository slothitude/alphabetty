import asyncio
import json
import logging
import re
from typing import AsyncIterator

import httpx

from config import settings

logger = logging.getLogger(__name__)

MODE_WRITING = {
    "concise": "Be concise and direct. Prioritize key facts. Keep responses focused.",
    "detailed": "Provide thorough, detailed explanations with examples and nuance.",
    "creative": "Be creative and engaging. Use vivid language and interesting analogies.",
    "academic": "Write in a formal academic style with precise terminology and structured arguments.",
    "code": "Focus on code and technical accuracy. Provide working implementations with clear explanations.",
}

SYSTEM_BASE = """You are Alphabetty, an AI research assistant. You provide accurate, well-sourced answers.

Rules:
- Always cite sources using [1], [2], etc. when referencing information from search results
- Be honest about uncertainty
- Generate exactly 3 relevant follow-up questions at the end of your response in a JSON block:
```followups
["question1", "question2", "question3"]
```
"""


async def build_context_block(conversation_id: int | None = None, query: str = "") -> str:
    """Build a dynamic context block from the knowledge graph for the system prompt."""
    parts = []

    try:
        from core.graph import get_recent_entities, get_conversation_entities, search_local

        # Recent entities (global — last 24h)
        recent = await get_recent_entities(hours=24, limit=5)
        if recent:
            parts.append("Recently discussed topics: " + ", ".join(
                f"{e['name']} ({e['type']})" for e in recent
            ))

        # Conversation-specific entities
        if conversation_id:
            conv_entities = await get_conversation_entities(conversation_id, limit=8)
            if conv_entities:
                parts.append("This conversation's entities: " + ", ".join(
                    f"{e['name']} ({e['type']})" for e in conv_entities
                ))

        # Related past conversations (FTS match on query)
        if query and len(query) > 5:
            try:
                # FTS5 doesn't handle special chars — sanitize
                safe_query = re.sub(r'[^\w\s]', ' ', query).strip()
                if safe_query:
                    related = await search_local(safe_query, limit=3)
                if related["conversations"]:
                    convs = [c["title"] for c in related["conversations"][:3]]
                    parts.append("Related past conversations: " + "; ".join(convs))
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"Context block build failed: {e}")

    if not parts:
        return ""

    return "\n\n[Knowledge Graph Context]\n" + "\n".join(f"- {p}" for p in parts)


async def _build_messages(query: str, sources: list[dict] | None = None, mode: str = "concise",
                          history: list[dict] | None = None,
                          conversation_id: int | None = None) -> list[dict]:
    system = SYSTEM_BASE + "\n" + MODE_WRITING.get(mode, MODE_WRITING["concise"])

    # Dynamic context from knowledge graph
    context_block = await build_context_block(conversation_id=conversation_id, query=query)
    if context_block:
        system += context_block

    if sources:
        source_text = "\n".join(
            f"[{i+1}] {s.get('title', 'Untitled')} — {s.get('url', '')}\n    {s.get('snippet', '')}"
            for i, s in enumerate(sources)
        )
        system += f"\n\nSources from search:\n{source_text}"

    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": query})
    return messages


# ─── Tool definitions for agent mode ───

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search the web using SearXNG. Returns a list of results with titles, URLs, and snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Max results to return (default 8)", "default": 8},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browse",
            "description": "Navigate Chrome to a URL and extract the page text content. Use this to read full articles, documentation, or any web page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to navigate to"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract",
            "description": "Extract structured data from the current Chrome page using a CSS selector or JavaScript expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector to extract text from (e.g. 'h1', '.article-body', '#content')"},
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Click an element on the current Chrome page. Use this to interact with pages — buttons, links, expand sections.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector of element to click"},
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into an input field on the current Chrome page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector of input field"},
                    "text": {"type": "string", "description": "Text to type"},
                },
                "required": ["selector", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "Take a screenshot of the current Chrome page. Returns a description of what's visible. Use when you need to understand visual layout or confirm page state.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "youtube_play",
            "description": "Play a YouTube video by searching for it. Navigates Chrome to YouTube, finds the best match, and plays it. Returns the video URL and ID. Use this whenever the user wants to watch or play a YouTube video.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "YouTube search query (song name, artist, etc.)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "video_play",
            "description": "Play any video from a URL (YouTube, Facebook, X/Twitter, Instagram, TikTok, etc.). Uses yt-dlp to extract a direct stream. Returns type (youtube/direct), stream URL, and metadata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Video URL to play (any platform)"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "macro_record",
            "description": "Start recording a macro of browser interactions. Give it a name and optional starting URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the macro"},
                    "url": {"type": "string", "description": "Optional starting URL for the macro", "default": ""},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "macro_stop",
            "description": "Stop recording the current macro and save it.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "macro_play",
            "description": "Replay a previously recorded macro by its ID.",
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
            "name": "tab_list",
            "description": "List all open Chrome tabs.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tab_new",
            "description": "Open a new Chrome tab and navigate to a URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to open in the new tab", "default": "about:blank"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll the current page up or down.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "description": "Direction to scroll: 'up' or 'down'", "enum": ["up", "down"]},
                    "amount": {"type": "integer", "description": "Pixels to scroll (default 300)", "default": 300},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait_for",
            "description": "Wait for an element matching a CSS selector to appear on the page. Useful after navigation or clicking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector to wait for"},
                    "timeout": {"type": "integer", "description": "Max wait in milliseconds (default 10000)", "default": 10000},
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "print_pdf",
            "description": "Print the current Chrome page as a PDF document.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": "Delegate a sub-task to another agent. The agent will process the task and return results. Use for parallel work or specialized capabilities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Description of the task to delegate"},
                    "agent": {"type": "string", "description": "Name of the agent to delegate to (optional, uses first available if omitted)"},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "signin_start",
            "description": "Start a sign-in flow for a website. Navigates to the URL, detects the login form, fills credentials, and submits. Returns the state (waiting_2fa, signed_in, or failed).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Login page URL"},
                    "username": {"type": "string", "description": "Email or username"},
                    "password": {"type": "string", "description": "Password"},
                },
                "required": ["url", "username", "password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "signin_2fa",
            "description": "Submit a 2FA/verification code during a sign-in flow. Use after signin_start returns waiting_2fa.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The 2FA verification code"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "signin_auto",
            "description": "Automatically sign in using a saved credential profile. Handles TOTP if configured. Returns signed_in or waiting_2fa.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the saved credential profile (e.g. 'google', 'github')"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workflow_list",
            "description": "List all n8n workflows. Returns name, ID, active status, and node count for each workflow.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workflow_create",
            "description": "Create a new n8n workflow from JSON nodes and connections. Use this to build persistent automations — scheduled tasks, webhooks, data pipelines, and multi-step workflows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Workflow name"},
                    "nodes": {"type": "array", "description": "Array of n8n node objects (each needs type, name, parameters, position)", "items": {"type": "object"}},
                    "connections": {"type": "object", "description": "Node connection map — keys are node names, values map output indexes to input targets"},
                    "active": {"type": "boolean", "description": "Whether to activate the workflow immediately (default false)", "default": False},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workflow_run",
            "description": "Trigger an n8n workflow execution by ID. Optionally pass input data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "The workflow ID to execute"},
                    "data": {"type": "object", "description": "Optional input data for the workflow"},
                },
                "required": ["workflow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workflow_status",
            "description": "Check execution history for an n8n workflow. Returns recent executions with status, duration, and timestamps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "The workflow ID to check"},
                    "limit": {"type": "integer", "description": "Max executions to return (default 10)", "default": 10},
                },
                "required": ["workflow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workflow_delete",
            "description": "Delete an n8n workflow by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "The workflow ID to delete"},
                },
                "required": ["workflow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download",
            "description": "Download a file or image from a URL. Returns the file as a download link for the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to download"},
                    "filename": {"type": "string", "description": "Optional filename override"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_save",
            "description": "Download a file from a URL and save it to the server's download directory. Use this to persist files to disk for later access.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to download"},
                    "filename": {"type": "string", "description": "Optional filename override"},
                    "subdir": {"type": "string", "description": "Optional subdirectory within the download folder"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_list",
            "description": "List files saved in the server's download directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subdir": {"type": "string", "description": "Optional subdirectory to list"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_models",
            "description": "List all available LLM models across providers (Z.ai, OpenRouter, NVIDIA NIM, Ollama). Shows model IDs, provider, and capabilities.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]

# ── Append media stack tools (fetched from OpenAI-compatible API) ──
try:
    from core.media import get_tools_sync
    _media_tool_defs = get_tools_sync()
    if _media_tool_defs:
        # Deduplicate by name
        existing_names = {t.get("function", {}).get("name") for t in AGENT_TOOLS}
        for mt in _media_tool_defs:
            if mt.get("function", {}).get("name") not in existing_names:
                AGENT_TOOLS.append(mt)
        logger.info(f"Added {len(_media_tool_defs)} media stack tools to agent")
except Exception as e:
    logger.warning(f"Media stack tools unavailable: {e}")

AGENT_SYSTEM = """You are Alphabetty, an autonomous AI research agent with full web browsing and automation capabilities.

## Available Tools

### Research
- **search(query)** — Search the web via SearXNG
- **browse(url)** — Navigate to a URL and extract page text
- **extract(selector)** — Extract text from a specific CSS selector
- **screenshot()** — Take a screenshot to see the current page

### Interaction
- **click(selector)** — Click an element on the current page
- **type_text(selector, text)** — Type into an input field
- **scroll(direction, amount)** — Scroll the page up or down
- **wait_for(selector, timeout)** — Wait for an element to appear

### Tab Management
- **tab_list()** — List all open Chrome tabs
- **tab_new(url)** — Open a new tab and navigate to URL

### Macros & Recording
- **macro_record(name, url)** — Start recording browser interactions as a macro
- **macro_stop()** — Stop recording and save the macro
- **macro_play(macro_id)** — Replay a saved macro

### Media
- **youtube_play(query)** — Search YouTube and play a video in Chrome
- **video_play(url)** — Play any video URL (YouTube, Facebook, X, Instagram, TikTok, etc.)
- **print_pdf()** — Print the current page as PDF

### Delegation
- **delegate(task, agent)** — Delegate a sub-task to another agent

### Sign-In
- **signin_start(url, username, password)** — Start sign-in flow for a website
- **signin_2fa(code)** — Submit 2FA code when prompted
- **signin_auto(name)** — Auto sign-in using saved credential profile

### Workflows
- **workflow_list()** — List all n8n workflows (name, ID, active, node count)
- **workflow_create(name, nodes, connections, active)** — Create a persistent n8n workflow from JSON
- **workflow_run(workflow_id, data)** — Trigger a workflow execution
- **workflow_status(workflow_id, limit)** — Check execution history for a workflow
- **workflow_delete(workflow_id)** — Delete a workflow

## Agent Strategy
1. Start by searching for the user's query
2. Browse the most relevant results to get detailed information
3. If you need more info, search again with refined queries
4. Click links, read pages, extract data as needed
5. Use wait_for() after navigation to ensure page content is loaded
6. Use tab management to work with multiple pages simultaneously
7. Delegate sub-tasks to other agents when parallel work is needed
8. Synthesize all findings into a comprehensive answer with citations

## Rules
- Always cite sources as [1], [2], etc.
- Be thorough — browse at least 2-3 pages for non-trivial questions
- If a page doesn't load or has little content, move to the next source
- Generate 3 follow-up questions at the end in a ```followups block
- You may make up to 15 tool calls to fully answer the question
"""


async def _call_llm_api(messages: list[dict], tools: list[dict] | None = None,
                        stream: bool = True) -> httpx.Response:
    """Low-level LLM API call. Returns the raw httpx response (streaming) or parsed JSON."""
    url = settings.llm_url
    key = settings.llm_api_key
    mdl = settings.llm_model

    payload = {
        "model": mdl,
        "messages": messages,
        "max_tokens": 16384,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))
    return client, await client.send(
        client.build_request("POST", url, json=payload, headers=headers),
        stream=stream,
    )


async def stream_llm(messages: list[dict], model: str | None = None,
                     base_url: str | None = None, api_key: str | None = None) -> AsyncIterator[str]:
    """Stream LLM response as SSE chunks. Uses provider router for fallback."""
    # If explicit overrides provided, use legacy direct-call path
    if base_url and api_key:
        async for chunk in _stream_direct(messages, model or settings.llm_model, base_url, api_key):
            yield chunk
        return

    # Route through provider router
    from core.providers import router as provider_router
    try:
        client, resp = await provider_router.call(
            messages, model=model, intent="chat", stream=True,
        )
        try:
            if resp.status_code == 429:
                raise httpx.HTTPStatusError("429", request=resp.request, response=resp)
            resp.raise_for_status()
            model_used = model or "unknown"
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    return
                try:
                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                    # Capture model from first chunk
                    if model_used == "unknown":
                        model_used = chunk.get("model", model_used)
                except json.JSONDecodeError:
                    continue
            logger.debug(f"LLM responded: {model_used} (provider router)")
        finally:
            await client.aclose()
    except RuntimeError as e:
        logger.error(f"All providers failed: {e}")
        yield f"[Error: All LLM providers failed — {e}]"


async def _stream_direct(messages: list[dict], model: str, url: str, key: str) -> AsyncIterator[str]:
    """Direct streaming call to a specific URL (legacy path)."""
    payload = {"model": model, "messages": messages, "max_tokens": 16384, "stream": True}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code == 429:
                    raise httpx.HTTPStatusError("429", request=resp.request, response=resp)
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue
                return
    except Exception as e:
        logger.warning(f"Direct LLM ({model}) failed: {e}, falling back to Ollama ({settings.ollama_model})")

    try:
        ollama_payload = {"model": settings.ollama_model, "messages": messages, "max_tokens": 16384, "stream": False}
        logger.info(f"LLM fallback: using {settings.ollama_model} via Ollama")
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            resp = await client.post(settings.ollama_url, json=ollama_payload,
                                     headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content:
                yield content
    except Exception as e:
        logger.error(f"Ollama fallback ({settings.ollama_model}) also failed: {e}")
        yield f"[Error: All LLM providers failed — {e}]"


async def call_llm(messages: list[dict], model: str | None = None,
                   base_url: str | None = None, api_key: str | None = None) -> str:
    """Non-streaming LLM call. Uses provider router for fallback."""
    # If explicit overrides provided, use legacy direct-call path
    if base_url and api_key:
        return await _call_direct(messages, model or settings.llm_model, base_url, api_key)

    # Route through provider router
    from core.providers import router as provider_router
    try:
        client, resp = await provider_router.call(
            messages, model=model, intent="planning", stream=False,
        )
        try:
            resp.raise_for_status()
            data = resp.json()
            msg = data["choices"][0]["message"]
            model_used = data.get("model", model or "unknown")
            logger.debug(f"LLM responded: {model_used} (provider router)")
            return msg.get("content", "") or msg.get("reasoning_content", "") or ""
        finally:
            await client.aclose()
    except Exception as e:
        logger.error(f"All providers failed: {e}")
        return f"[Error: All LLM providers failed]"


async def _call_direct(messages: list[dict], model: str, url: str, key: str) -> str:
    """Direct non-streaming call (legacy path with retries)."""
    payload = {"model": model, "messages": messages, "max_tokens": 16384}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 429:
                    await asyncio.sleep(30 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                msg = data["choices"][0]["message"]
                logger.debug(f"LLM responded: {model} (primary)")
                return msg.get("content", "") or msg.get("reasoning_content", "") or ""
        except Exception as e:
            logger.warning(f"Primary LLM ({model}) attempt {attempt+1} failed: {e}")

    try:
        ollama_payload = {"model": settings.ollama_model, "messages": messages, "max_tokens": 16384}
        logger.info(f"LLM fallback: using {settings.ollama_model} via Ollama")
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            resp = await client.post(settings.ollama_url, json=ollama_payload,
                                     headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            data = resp.json()
            logger.debug(f"LLM responded: {settings.ollama_model} (fallback)")
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        logger.error(f"Ollama fallback failed: {e}")
        return f"[Error: All LLM providers failed]"


async def call_llm_with_tools(messages: list[dict], tools: list[dict] | None = None) -> dict:
    """Non-streaming LLM call with tool support. Uses provider router for reliable tool calling."""
    from core.providers import router as provider_router

    try:
        client, resp = await provider_router.call(
            messages, intent="agent", stream=False,
            tools=tools or AGENT_TOOLS,
        )
        try:
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]
        finally:
            await client.aclose()
    except Exception as e:
        logger.error(f"Tool-calling LLM failed via provider router: {e}")
        raise


# ─── Smart Tool Routing ───

# Tool groups: core tools are always included, extras are activated by keyword match
_TOOL_GROUPS = {
    "core": {
        "search", "browse", "extract", "click", "type_text", "screenshot",
        "scroll", "wait_for", "tab_list", "tab_new", "delegate", "download",
        "download_save", "download_list", "list_models",
    },
    "media": {
        "youtube_play", "video_play",
        "media_search", "search_tmdb", "media_request", "media_requests",
        "radarr_movies", "radarr_queue", "sonarr_series", "sonarr_queue",
        "torrents_list", "torrents_action", "stack_status", "vpn_status",
        "media_control",
    },
    "macro": {
        "macro_record", "macro_stop", "macro_play",
    },
    "signin": {
        "signin_start", "signin_2fa", "signin_auto",
    },
    "utility": {
        "print_pdf",
    },
    "workflow": {
        "workflow_list", "workflow_create", "workflow_run", "workflow_status", "workflow_delete",
    },
}

# Keyword → group mapping for intent detection
_INTENT_KEYWORDS = {
    "media": [
        "play", "video", "watch", "youtube", "yt", "music", "song", "movie", "clip",
        "stream", "listen", "audio", "facebook video", "instagram video", "tiktok",
        "x video", "twitter video", "embed", "jellyfin", "film", "show", "episode",
        "series", "season", "tv show", "tv series", "torrent", "download", "radarr",
        "sonarr", "qbittorrent", "request", "tmdb", "vpn",
        "media", "library", "catalog", "collection",
    ],
    "macro": [
        "macro", "record", "replay", "automate", "repeat",
    ],
    "signin": [
        "sign in", "login", "log in", "authenticate", "2fa", "totp", "credential",
        "password", "signin",
    ],
    "utility": [
        "pdf", "print", "export", "download", "save", "fetch file", "save file", "grab file",
    ],
    "workflow": [
        "workflow", "automate", "schedule", "cron", "webhook", "trigger",
        "recurring", "pipeline", "notification", "n8n", "automation",
    ],
}

# Pre-built lookup: tool_name → tool definition
_TOOL_BY_NAME: dict[str, dict] = {}


def _init_tool_index():
    """Build name→tool lookup on first call."""
    if _TOOL_BY_NAME:
        return
    for t in AGENT_TOOLS:
        name = t.get("function", {}).get("name", "")
        if name:
            _TOOL_BY_NAME[name] = t


def select_tools(query: str, history: list[dict] | None = None) -> list[dict]:
    """Select the minimal tool subset based on query intent.

    Always includes core tools. Adds group-specific tools when keywords match.
    Also scans recent conversation history for sustained intent.
    Returns the tool definitions list for the LLM.
    """
    _init_tool_index()

    # Gather all text to scan: query + last few user messages
    text = query.lower()
    if history:
        for msg in history[-4:]:
            if msg.get("role") == "user":
                text += " " + (msg.get("content", "") or "").lower()

    # Determine which groups to activate
    active_groups = {"core"}
    for group, keywords in _INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                active_groups.add(group)
                break

    # Collect tool names from active groups
    tool_names = set()
    for group in active_groups:
        tool_names.update(_TOOL_GROUPS.get(group, set()))

    # Build the tools list, preserving original order
    selected = []
    for t in AGENT_TOOLS:
        name = t.get("function", {}).get("name", "")
        if name in tool_names:
            selected.append(t)

    logger.info(f"Tool routing: {len(selected)}/{len(AGENT_TOOLS)} tools selected "
                f"(groups: {active_groups}) for query: {query[:60]}")
    return selected


def build_system_prompt(tools: list[dict]) -> str:
    """Build AGENT_SYSTEM with only the available tools documented."""
    available = {t["function"]["name"] for t in tools}

    sections = []
    sections.append("""You are Alphabetty, an autonomous AI research agent with full web browsing and automation capabilities.

## Available Tools""")

    # Research section — always present (core)
    research_tools = []
    if "search" in available:
        research_tools.append("- **search(query)** — Search the web via SearXNG")
    if "browse" in available:
        research_tools.append("- **browse(url)** — Navigate to a URL and extract page text")
    if "extract" in available:
        research_tools.append("- **extract(selector)** — Extract text from a specific CSS selector")
    if "screenshot" in available:
        research_tools.append("- **screenshot()** — Take a screenshot to see the current page")

    if research_tools:
        sections.append("### Research\n" + "\n".join(research_tools))

    interaction_tools = []
    if "click" in available:
        interaction_tools.append("- **click(selector)** — Click an element on the current page")
    if "type_text" in available:
        interaction_tools.append("- **type_text(selector, text)** — Type into an input field")
    if "scroll" in available:
        interaction_tools.append("- **scroll(direction, amount)** — Scroll the page up or down")
    if "wait_for" in available:
        interaction_tools.append("- **wait_for(selector, timeout)** — Wait for an element to appear")

    if interaction_tools:
        sections.append("### Interaction\n" + "\n".join(interaction_tools))

    tab_tools = []
    if "tab_list" in available:
        tab_tools.append("- **tab_list()** — List all open Chrome tabs")
    if "tab_new" in available:
        tab_tools.append("- **tab_new(url)** — Open a new tab and navigate to URL")

    if tab_tools:
        sections.append("### Tab Management\n" + "\n".join(tab_tools))

    # Optional sections
    macro_tools = []
    if "macro_record" in available:
        macro_tools.append("- **macro_record(name, url)** — Start recording browser interactions as a macro")
    if "macro_stop" in available:
        macro_tools.append("- **macro_stop()** — Stop recording and save the macro")
    if "macro_play" in available:
        macro_tools.append("- **macro_play(macro_id)** — Replay a saved macro")

    if macro_tools:
        sections.append("### Macros & Recording\n" + "\n".join(macro_tools))

    media_tools = []
    if "youtube_play" in available:
        media_tools.append("- **youtube_play(query)** — Search YouTube and play a video in Chrome")
    if "video_play" in available:
        media_tools.append("- **video_play(url)** — Play any video URL (YouTube, Facebook, X, TikTok, etc.)")
    if "media_search" in available:
        media_tools.append("- **media_search(query)** — Search Jellyfin library, returns direct-play stream links")
    if "search_tmdb" in available:
        media_tools.append("- **search_tmdb(query, media_type)** — Search TMDb for movies/TV to request")
    if "media_request" in available:
        media_tools.append("- **media_request(media_id, media_type)** — Request a movie/TV show via Jellyseerr")
    if "media_requests" in available:
        media_tools.append("- **media_requests(status)** — List Jellyseerr requests")
    if "radarr_movies" in available:
        media_tools.append("- **radarr_movies(status)** — List Radarr movies")
    if "sonarr_series" in available:
        media_tools.append("- **sonarr_series(status)** — List Sonarr series")
    if "torrents_list" in available:
        media_tools.append("- **torrents_list(filter_status)** — List qBittorrent torrents")
    if "torrents_action" in available:
        media_tools.append("- **torrents_action(hash, action)** — Pause/resume/delete/bump a torrent")
    if "media_control" in available:
        media_tools.append("- **media_control(action, query, hash, service)** — Swiss army knife: search, downloads, status, etc.")
    if any(t in available for t in ("media_search", "media_control")):
        media_tools.append("- **IMPORTANT: Do NOT browse Jellyfin/Radarr/Sonarr URLs directly. Always use `media_search`, `search_tmdb`, `media_request` etc. tools instead.**")
        media_tools.append("- **Use `play_remote` (Tailscale) URLs for playback, NOT `play_lan`. Use `video_play(url)` to play — it opens the native player with controls.**")

    if media_tools:
        sections.append("### Media\n" + "\n".join(media_tools))

    signin_tools = []
    if "signin_start" in available:
        signin_tools.append("- **signin_start(url, username, password)** — Start sign-in flow for a website")
    if "signin_2fa" in available:
        signin_tools.append("- **signin_2fa(code)** — Submit 2FA code when prompted")
    if "signin_auto" in available:
        signin_tools.append("- **signin_auto(name)** — Auto sign-in using saved credential profile")

    if signin_tools:
        sections.append("### Sign-In\n" + "\n".join(signin_tools))

    utility_tools = []
    if "delegate" in available:
        utility_tools.append("- **delegate(task, agent)** — Delegate a sub-task to another agent")
    if "print_pdf" in available:
        utility_tools.append("- **print_pdf()** — Print the current page as PDF")
    if "download" in available:
        utility_tools.append("- **download(url, filename)** — Download a file from a URL (returns download link)")
    if "download_save" in available:
        utility_tools.append("- **download_save(url, filename, subdir)** — Download and save file to disk")
    if "download_list" in available:
        utility_tools.append("- **download_list(subdir)** — List saved files in download directory")
    if "list_models" in available:
        utility_tools.append("- **list_models()** — List all available LLM models across providers")

    if utility_tools:
        sections.append("### Utility\n" + "\n".join(utility_tools))

    workflow_tools = []
    if "workflow_list" in available:
        workflow_tools.append("- **workflow_list()** — List all n8n workflows (name, ID, active, node count)")
    if "workflow_create" in available:
        workflow_tools.append("- **workflow_create(name, nodes, connections, active)** — Create a persistent n8n workflow from JSON")
    if "workflow_run" in available:
        workflow_tools.append("- **workflow_run(workflow_id, data)** — Trigger a workflow execution")
    if "workflow_status" in available:
        workflow_tools.append("- **workflow_status(workflow_id, limit)** — Check execution history for a workflow")
    if "workflow_delete" in available:
        workflow_tools.append("- **workflow_delete(workflow_id)** — Delete a workflow")

    if workflow_tools:
        sections.append("### Workflows\n" + "\n".join(workflow_tools))

    # Rules — always present
    sections.append("""## Agent Strategy
1. Start by searching for the user's query
2. Browse the most relevant results to get detailed information
3. If you need more info, search again with refined queries
4. Click links, read pages, extract data as needed
5. Use wait_for() after navigation to ensure page content is loaded
6. Use tab management to work with multiple pages simultaneously
7. Delegate sub-tasks to other agents when parallel work is needed
8. Synthesize all findings into a comprehensive answer with citations

## Rules
- Always cite sources as [1], [2], etc.
- Be thorough — browse at least 2-3 pages for non-trivial questions
- If a page doesn't load or has little content, move to the next source
- Generate 3 follow-up questions at the end in a ```followups block
- You may make up to 15 tool calls to fully answer the question""")

    return "\n\n".join(sections)
