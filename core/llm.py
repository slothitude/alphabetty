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
]

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
- **print_pdf()** — Print the current page as PDF

### Delegation
- **delegate(task, agent)** — Delegate a sub-task to another agent

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
    """Stream LLM response as SSE chunks. Falls back from Z.ai to Ollama on failure."""
    url = base_url or settings.llm_url
    key = api_key or settings.llm_api_key
    mdl = model or settings.llm_model

    payload = {
        "model": mdl,
        "messages": messages,
        "max_tokens": 16384,
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

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
        logger.warning(f"Primary LLM failed: {e}, falling back to Ollama")

    try:
        ollama_payload = {
            "model": settings.ollama_model,
            "messages": messages,
            "max_tokens": 16384,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            resp = await client.post(settings.ollama_url, json=ollama_payload,
                                     headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content:
                yield content
    except Exception as e:
        logger.error(f"Ollama fallback also failed: {e}")
        yield f"[Error: All LLM providers failed — {e}]"


async def call_llm(messages: list[dict], model: str | None = None,
                   base_url: str | None = None, api_key: str | None = None) -> str:
    """Non-streaming LLM call. Falls back from Z.ai to Ollama."""
    url = base_url or settings.llm_url
    key = api_key or settings.llm_api_key
    mdl = model or settings.llm_model

    payload = {
        "model": mdl,
        "messages": messages,
        "max_tokens": 16384,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

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
                return msg.get("content", "") or msg.get("reasoning_content", "") or ""
        except Exception as e:
            logger.warning(f"Primary LLM attempt {attempt+1} failed: {e}")

    try:
        ollama_payload = {"model": settings.ollama_model, "messages": messages, "max_tokens": 16384}
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            resp = await client.post(settings.ollama_url, json=ollama_payload,
                                     headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        logger.error(f"Ollama fallback failed: {e}")
        return f"[Error: All LLM providers failed]"


async def call_llm_with_tools(messages: list[dict]) -> dict:
    """Non-streaming LLM call with tool support. Returns full response with tool_calls."""
    url = settings.llm_url
    key = settings.llm_api_key
    mdl = settings.llm_model

    payload = {
        "model": mdl,
        "messages": messages,
        "max_tokens": 16384,
        "tools": AGENT_TOOLS,
        "tool_choice": "auto",
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]
