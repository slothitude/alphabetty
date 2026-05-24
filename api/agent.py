"""Agent mode — Autonomous AI agent with tool calling, planning, and reflection.

The LLM decides when to search, browse, click, extract. It runs in a loop:
1. LLM receives query + tool definitions
2. LLM decides which tool(s) to call (or gives final answer)
3. Tools are executed, results fed back to LLM
4. Repeat until LLM gives a final text answer
5. Stream all steps + final answer to the client via SSE
"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import async_session
from core.auth import get_current_user
from core.llm import AGENT_SYSTEM, call_llm, call_llm_with_tools, select_tools, build_system_prompt
from core.searxng import adaptive_search as searxng_search
from core.content_extractor import fetch_and_extract
from core.source_citer import format_sources
from models.conversation import Conversation, Message
from models.user import User

router = APIRouter(tags=["agent"])
logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 15
REFLECT_EVERY_N = 5

# Per-session tab tracking — keyed by conversation ID so multiple agents don't collide
_session_tabs: dict[int, str | None] = {}


class AgentSession:
    """Per-request agent context — isolates tab state between concurrent agents."""

    def __init__(self, conv_id: int):
        self.conv_id = conv_id
        self.active_tab_id: str | None = _session_tabs.get(conv_id)

    def set_active_tab(self, tab_id: str):
        self.active_tab_id = tab_id
        _session_tabs[self.conv_id] = tab_id

    def get_tab_id(self) -> str | None:
        return self.active_tab_id


class AgentRequest(BaseModel):
    query: str
    conversation_id: Optional[int] = None
    mode: str = "detailed"


# ─── Planning & Reflection ───

async def plan_step(query: str) -> str:
    """LLM breaks the query into sub-questions and prioritizes tools."""
    messages = [
        {"role": "system", "content": """You are a planning assistant for an AI agent with these capabilities:
- search(query) — web search
- browse(url) — navigate and read pages
- extract(selector) — extract page content
- click(selector) / type_text(selector, text) — interact with pages
- scroll(direction) / wait_for(selector) — navigate pages
- screenshot() — see current page
- tab_list() / tab_new(url) — manage Chrome tabs
- macro_record(name) / macro_stop() / macro_play(id) — record & replay macros
- youtube_play(query) — play YouTube videos
- video_play(url) — play any video URL (YouTube, Facebook, X, TikTok, etc.)
- print_pdf() — print page as PDF
- delegate(task, agent) — delegate sub-tasks to other agents
- signin_start(url, username, password) — start sign-in flow for a website
- signin_2fa(code) — submit 2FA code
- signin_auto(name) — auto sign-in using saved credential
- workflow_list() — list n8n workflows
- workflow_create(name, nodes, connections, active) — create n8n workflow
- workflow_run(workflow_id, data) — trigger workflow execution
- workflow_status(workflow_id, limit) — check execution history
- workflow_delete(workflow_id) — delete a workflow
- list_models() — list all available LLM models across providers

Given a user query, briefly state which tools to use and in what order. 1-2 sentences max."""},
        {"role": "user", "content": f"Plan approach for: {query}"},
    ]
    return await call_llm(messages)


async def reflect_step(query: str, rounds_done: int, tool_log: list[dict]) -> str:
    """LLM assesses progress and suggests adjustments."""
    log_summary = "\n".join(
        f"- {t['tool']}({json.dumps(t['args'])[:80]})" for t in tool_log[-5:]
    )
    messages = [
        {"role": "system", "content": "You are a research progress reviewer. In 1-2 sentences, assess what's been found and what still needs investigation."},
        {"role": "user", "content": f"Query: {query}\nRounds done: {rounds_done}\nRecent tool calls:\n{log_summary}"},
    ]
    return await call_llm(messages)


# ─── Tool executors ───

async def execute_tool(name: str, args: dict, session: AgentSession | None = None) -> dict:
    """Execute a tool call and return the result."""
    # Swarm routing: delegate to peer if local instance lacks capability
    try:
        from core.swarm import swarm
        target = swarm.route_tool(name, args)
        if target:
            result = await swarm.execute_remote(target, name, args)
            result["swarm_routed"] = target.name
            return result
    except Exception as e:
        logger.debug(f"Swarm routing failed for {name}, falling back to local: {e}")

    try:
        if name == "search":
            results = await searxng_search(args["query"], max_results=args.get("max_results", 8))
            formatted = format_sources(results, query=args.get("query", ""))
            return {
                "tool": "search",
                "results": formatted,
                "count": len(formatted),
            }

        elif name == "browse":
            tid = session.get_tab_id() if session else None
            if tid:
                # Use CDP to navigate and extract — handles JS-heavy pages
                from core.cdp_bridge import cdp
                await cdp.navigate(args["url"], tab_id=tid)
                await asyncio.sleep(2)
                title = await cdp.evaluate("document.title", tab_id=tid)
                content = await cdp.get_content(tab_id=tid)
                return {
                    "tool": "browse",
                    "url": args["url"],
                    "title": title or "",
                    "text": (content or "")[:12000],
                }
            else:
                # Fallback: HTTP fetch
                content = await fetch_and_extract(args["url"])
                return {
                    "tool": "browse",
                    "url": args["url"],
                    "title": content.get("title", ""),
                    "text": content.get("text", "")[:12000],
                    "error": content.get("error"),
                }

        elif name == "extract":
            from core.cdp_bridge import cdp
            tid = session.get_tab_id() if session else None
            expr = f'document.querySelector("{args["selector"]}")?.innerText || "Element not found"'
            result = await cdp.evaluate(expr, tab_id=tid)
            return {"tool": "extract", "selector": args["selector"], "text": result or "Not found"}

        elif name == "click":
            from core.cdp_bridge import cdp
            tid = session.get_tab_id() if session else None
            result = await cdp.click(args["selector"], tab_id=tid)
            return {"tool": "click", "selector": args["selector"], "result": result}

        elif name == "type_text":
            from core.cdp_bridge import cdp
            tid = session.get_tab_id() if session else None
            result = await cdp.type_text(args["selector"], args["text"], tab_id=tid)
            return {"tool": "type_text", "selector": args["selector"], "result": result}

        elif name == "screenshot":
            from core.cdp_bridge import cdp
            tid = session.get_tab_id() if session else None
            try:
                content = await cdp.get_content(tab_id=tid)
                return {"tool": "screenshot", "page_text_preview": (content or "")[:3000]}
            except Exception as e:
                return {"tool": "screenshot", "error": str(e)}

        elif name == "youtube_play":
            from core.cdp_bridge import cdp
            from urllib.parse import urlparse, parse_qs

            query_str = args["query"]

            # Use yt-dlp to search YouTube — much more reliable than DOM scraping
            def _yt_search():
                import yt_dlp
                ydl_opts = {"quiet": True, "extract_flat": True, "default_search": "ytsearch1"}
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    results = ydl.extract_info(f"ytsearch1:{query_str}", download=False)
                    entries = results.get("entries", [])
                    if entries:
                        return entries[0].get("url") or entries[0].get("webpage_url")
                return None

            video_url = await asyncio.get_event_loop().run_in_executor(None, _yt_search)

            if not video_url:
                return {"tool": "youtube_play", "error": f"No video found for: {query_str}"}

            if not video_url.startswith("http"):
                video_url = f"https://www.youtube.com{video_url}"

            parsed = urlparse(video_url)
            video_id = parse_qs(parsed.query).get("v", [None])[0] or parsed.path.split("/")[-1]

            # Navigate Chrome to play it
            await cdp.navigate(video_url)

            # Emit event
            from core.events import emit
            emit("youtube.playing", {"query": query_str, "video_url": video_url, "video_id": video_id})

            return {"tool": "youtube_play", "query": query_str, "video_url": video_url, "video_id": video_id}

        elif name == "video_play":
            import httpx as _httpx

            async with _httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "http://localhost:7700/api/cdp/video/play",
                    json={"url": args["url"]},
                )
                result = resp.json()

            # Emit event for frontend
            from core.events import emit
            emit("video.playing", result)

            return {"tool": "video_play", **result}

        elif name == "macro_record":
            from core.macro import recorder
            result = recorder.start(args["name"], args.get("url", ""))
            return {"tool": "macro_record", **result}

        elif name == "macro_stop":
            from core.macro import recorder
            result = await recorder.stop()
            from core.events import emit
            emit("macro.done", result)
            return {"tool": "macro_stop", **result}

        elif name == "macro_play":
            from core.macro import recorder
            result = await recorder.play(args["macro_id"])
            from core.events import emit
            emit("macro.done", result)
            return {"tool": "macro_play", **result}

        elif name == "tab_list":
            from core.cdp_bridge import cdp
            tabs = await cdp.get_tabs()
            return {"tool": "tab_list", "tabs": tabs, "count": len(tabs)}

        elif name == "tab_new":
            from core.cdp_bridge import cdp
            result = await cdp.create_tab(args.get("url", "about:blank"))
            # Set as active tab for this session
            if result.get("targetId") and session:
                session.set_active_tab(result["targetId"])
            from core.events import emit
            emit("tab.created", result)
            return {"tool": "tab_new", **result}

        elif name == "scroll":
            from core.cdp_bridge import cdp
            tid = session.get_tab_id() if session else None
            direction = args.get("direction", "down")
            amount = args.get("amount", 300)
            y = -amount if direction == "up" else amount
            result = await cdp.scroll(0, y, tab_id=tid)
            return {"tool": "scroll", **result}

        elif name == "wait_for":
            from core.cdp_bridge import cdp
            tid = session.get_tab_id() if session else None
            result = await cdp.wait_for_selector(args["selector"], args.get("timeout", 10000), tab_id=tid)
            return {"tool": "wait_for", **result}

        elif name == "print_pdf":
            from core.cdp_bridge import cdp
            tid = session.get_tab_id() if session else None
            result = await cdp.print_pdf(tab_id=tid)
            return {"tool": "print_pdf", **result}

        elif name == "delegate":
            from core.agent_bridge import delegate
            result = await delegate(args["task"], args.get("agent"))
            return {"tool": "delegate", **result}

        elif name == "signin_start":
            from core.signin import workflow
            tid = session.get_tab_id() if session else None
            result = await workflow.start(
                url=args["url"],
                username=args["username"],
                password=args["password"],
                tab_id=tid,
            )
            return {"tool": "signin_start", **result}

        elif name == "signin_2fa":
            from core.signin import workflow
            tid = session.get_tab_id() if session else None
            result = await workflow.submit_2fa(args["code"], tab_id=tid)
            return {"tool": "signin_2fa", **result}

        elif name == "signin_auto":
            from core.signin import workflow
            tid = session.get_tab_id() if session else None
            result = await workflow.auto_signin(args["name"], tab_id=tid)
            return {"tool": "signin_auto", **result}

        elif name == "workflow_list":
            from core import n8n
            result = await n8n.list_workflows()
            return {"tool": "workflow_list", **result}

        elif name == "workflow_create":
            from core import n8n
            result = await n8n.create_workflow(
                name=args["name"],
                nodes=args.get("nodes"),
                connections=args.get("connections"),
                active=args.get("active", False),
            )
            return {"tool": "workflow_create", **result}

        elif name == "workflow_run":
            from core import n8n
            result = await n8n.execute_workflow(args["workflow_id"], data=args.get("data"))
            return {"tool": "workflow_run", **result}

        elif name == "workflow_status":
            from core import n8n
            result = await n8n.list_executions(args["workflow_id"], limit=args.get("limit", 10))
            return {"tool": "workflow_status", **result}

        elif name == "workflow_delete":
            from core import n8n
            result = await n8n.delete_workflow(args["workflow_id"])
            return {"tool": "workflow_delete", **result}

        elif name == "download":
            from urllib.parse import quote as _url_quote
            url = args["url"]
            filename = args.get("filename", "")
            # Use localhost since we're inside the same container
            download_url = f"http://localhost:7700/api/download/proxy?url={_url_quote(url, safe='')}"
            return {
                "tool": "download",
                "url": url,
                "download_url": download_url,
                "message": f"File ready for download at: {download_url}",
            }

        elif name == "download_save":
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=_httpx.Timeout(120.0, connect=10.0)) as client:
                payload = {"url": args["url"]}
                if args.get("filename"):
                    payload["filename"] = args["filename"]
                if args.get("subdir"):
                    payload["subdir"] = args["subdir"]
                r = await client.post("http://localhost:7700/api/download/save", json=payload)
                result = r.json()
            return {"tool": "download_save", **result}

        elif name == "download_list":
            import httpx as _httpx
            params = {}
            if args.get("subdir"):
                params["subdir"] = args["subdir"]
            async with _httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get("http://localhost:7700/api/download/files", params=params)
                result = r.json()
            return {"tool": "download_list", **result}

        elif name == "list_models":
            from core.providers import router as provider_router
            models = provider_router.list_models()
            return {"tool": "list_models", "models": models, "total": len(models)}

        else:
            return {"error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.error(f"Tool '{name}' failed: {e}")
        return {"tool": name, "error": str(e)}


def format_tool_result_for_llm(tool_result: dict) -> str:
    """Format tool execution result as a string for the LLM."""
    if "error" in tool_result and tool_result.get("tool") != "browse":
        return f"Error: {tool_result['error']}"

    tool = tool_result.get("tool", "unknown")

    if tool == "search":
        results = tool_result.get("results", [])
        if not results:
            return "No search results found."
        if results and results[0].get("error"):
            return f"Search failed: {results[0]['snippet']}"
        lines = [f"Found {len(results)} results:"]
        for r in results:
            lines.append(f"[{r['index']}] {r['title']} — {r['url']}\n    {r['snippet'][:200]}")
        return "\n".join(lines)

    elif tool == "browse":
        title = tool_result.get("title", "Untitled")
        text = tool_result.get("text", "")
        url = tool_result.get("url", "")
        error = tool_result.get("error")
        if error:
            return f"Failed to browse {url}: {error}"
        return f"Page: {title}\nURL: {url}\n\nContent:\n{text[:10000]}"

    elif tool == "extract":
        return f"Extracted from '{tool_result.get('selector', '')}':\n{tool_result.get('text', '')[:5000]}"

    elif tool == "click":
        result = tool_result.get("result", {})
        if "error" in result:
            return f"Click failed: {result['error']}"
        return f"Clicked '{tool_result.get('selector', '')}' at ({result.get('x', '?')}, {result.get('y', '?')})"

    elif tool == "type_text":
        return f"Typed into '{tool_result.get('selector', '')}'"

    elif tool == "screenshot":
        preview = tool_result.get("page_text_preview", "")
        if preview:
            return f"Current page content (first 2000 chars):\n{preview}"
        return f"Screenshot taken. Error: {tool_result.get('error', 'unknown')}"

    elif tool == "youtube_play":
        vid = tool_result.get("video_id", "")
        if tool_result.get("error"):
            return f"YouTube play failed: {tool_result['error']}"
        return f"Now playing YouTube video: {tool_result.get('video_url', '')} (video_id: {vid})"

    elif tool == "video_play":
        vtype = tool_result.get("type", "unknown")
        if tool_result.get("error"):
            return f"Video play failed: {tool_result['error']}"
        if vtype == "youtube":
            return f"Playing YouTube video: {tool_result.get('video_id', '')}"
        if vtype == "direct":
            return f"Playing video: {tool_result.get('title', tool_result.get('url', ''))} (direct stream)"
        return f"Video sent to Chrome: {tool_result.get('url', '')}"

    elif tool == "macro_record":
        return f"Macro recording started: {tool_result.get('name', 'unnamed')}"

    elif tool == "macro_stop":
        if tool_result.get("error"):
            return f"Macro stop failed: {tool_result['error']}"
        return f"Macro saved: {tool_result.get('name', 'unnamed')} (id={tool_result.get('id')}, {tool_result.get('step_count', 0)} steps)"

    elif tool == "macro_play":
        if tool_result.get("error"):
            return f"Macro play failed: {tool_result['error']}"
        return f"Macro played: {tool_result.get('macro_name', '')} ({tool_result.get('steps_played', 0)}/{tool_result.get('steps_total', 0)} steps)"

    elif tool == "tab_list":
        tabs = tool_result.get("tabs", [])
        lines = [f"Open tabs ({len(tabs)}):"]
        for t in tabs:
            lines.append(f"  - [{t.get('id', '?')[:8]}] {t.get('title', 'Untitled')[:60]} — {t.get('url', '')[:80]}")
        return "\n".join(lines)

    elif tool == "tab_new":
        return f"New tab created: {tool_result.get('url', 'about:blank')} (targetId: {tool_result.get('targetId', '')})"

    elif tool == "scroll":
        return f"Scrolled {tool_result.get('x', 0)},{tool_result.get('y', 300)}"

    elif tool == "wait_for":
        if tool_result.get("status") == "timeout":
            return f"Timeout waiting for element: {tool_result.get('selector')}"
        return f"Element found: {tool_result.get('selector')} (waited {tool_result.get('waited_ms', 0)}ms)"

    elif tool == "print_pdf":
        if tool_result.get("error"):
            return f"PDF print failed: {tool_result['error']}"
        return f"PDF generated: {tool_result.get('size_bytes', 0)} bytes"

    elif tool == "delegate":
        if tool_result.get("error"):
            return f"Delegation failed: {tool_result['error']}"
        return f"Delegated to {tool_result.get('agent', 'unknown')}:\n{tool_result.get('result', '')[:3000]}"

    elif tool == "signin_start":
        state = tool_result.get("state", "unknown")
        if tool_result.get("error"):
            return f"Sign-in failed: {tool_result['error']}"
        if state == "waiting_2fa":
            hint = tool_result.get("hint", "")
            return f"Sign-in requires 2FA. Hint: {hint}. Use signin_2fa(code) to submit the code."
        if state == "signed_in":
            return f"Successfully signed in to {tool_result.get('site', 'site')}"
        return f"Sign-in state: {state}"

    elif tool == "signin_2fa":
        if tool_result.get("error"):
            return f"2FA submission failed: {tool_result['error']}"
        if tool_result.get("state") == "signed_in":
            return "2FA accepted — successfully signed in"
        return f"2FA result: {tool_result.get('state', 'unknown')}"

    elif tool == "signin_auto":
        if tool_result.get("error"):
            return f"Auto sign-in failed: {tool_result['error']}"
        if tool_result.get("totp_auto"):
            return "Auto sign-in with TOTP — successfully signed in"
        if tool_result.get("state") == "signed_in":
            return "Auto sign-in successful"
        if tool_result.get("state") == "waiting_2fa":
            return "Auto sign-in requires manual 2FA — use signin_2fa(code)"
        return f"Auto sign-in state: {tool_result.get('state', 'unknown')}"

    elif tool == "workflow_list":
        if tool_result.get("error"):
            return f"Workflow list failed: {tool_result['error']}"
        workflows = tool_result.get("data", [])
        if not workflows:
            return "No workflows found."
        lines = [f"Workflows ({len(workflows)}):"]
        for wf in workflows:
            active = "active" if wf.get("active") else "inactive"
            nodes = len(wf.get("nodes", []))
            lines.append(f"  - [{wf.get('id')}] {wf.get('name')} ({active}, {nodes} nodes)")
        return "\n".join(lines)

    elif tool == "workflow_create":
        if tool_result.get("error"):
            return f"Workflow create failed: {tool_result['error']}"
        wf = tool_result.get("data", tool_result)
        return f"Workflow created: {wf.get('name', 'unnamed')} (id={wf.get('id')}, active={wf.get('active', False)})"

    elif tool == "workflow_run":
        if tool_result.get("error"):
            return f"Workflow run failed: {tool_result['error']}"
        return f"Workflow execution triggered: {json.dumps(tool_result)[:500]}"

    elif tool == "workflow_status":
        if tool_result.get("error"):
            return f"Workflow status failed: {tool_result['error']}"
        executions = tool_result.get("data", [])
        if not executions:
            return "No executions found for this workflow."
        lines = [f"Executions ({len(executions)}):"]
        for ex in executions:
            status = ex.get("status", "unknown")
            finished = ex.get("stoppedAt", "running")
            lines.append(f"  - [{ex.get('id')}] {status} at {finished}")
        return "\n".join(lines)

    elif tool == "workflow_delete":
        if tool_result.get("error"):
            return f"Workflow delete failed: {tool_result['error']}"
        return "Workflow deleted successfully"

    elif tool == "download":
        if tool_result.get("error"):
            return f"Download failed: {tool_result['error']}"
        return f"File ready for download: {tool_result.get('download_url', '')}"

    elif tool == "download_save":
        if tool_result.get("error"):
            return f"Save failed: {tool_result['error']}"
        size = tool_result.get("size_bytes", 0)
        return f"Saved: {tool_result.get('path', '?')} ({size:,} bytes, {tool_result.get('content_type', 'unknown')})"

    elif tool == "download_list":
        files = tool_result.get("files", [])
        if not files:
            return "Download directory is empty"
        lines = [f"Saved files ({len(files)}):"]
        for f in files:
            lines.append(f"  - {f['name']} ({f['size_bytes']:,} bytes)")
        return "\n".join(lines)

    elif tool == "list_models":
        models = tool_result.get("models", [])
        if not models:
            return "No models available"
        grouped = {}
        for m in models:
            grouped.setdefault(m["provider"], []).append(m["id"])
        lines = [f"Available models ({len(models)} total):"]
        for provider, ids in grouped.items():
            lines.append(f"  [{provider}] {', '.join(ids[:10])}")
            if len(ids) > 10:
                lines.append(f"    ... and {len(ids) - 10} more")
        return "\n".join(lines)

    return json.dumps(tool_result)


@router.post("/agent")
async def agent_chat(req: AgentRequest, user: User = Depends(get_current_user)):
    """Autonomous agent: LLM decides tools to call, executes them, loops until answer."""

    # Get or create conversation
    conv = None
    async with async_session() as db:
        if req.conversation_id:
            result = await db.execute(select(Conversation).where(Conversation.id == req.conversation_id))
            conv = result.scalar_one_or_none()
        if not conv:
            conv = Conversation(title=f"Agent: {req.query[:80]}", mode=req.mode, user_id=user.id)
            db.add(conv)
            await db.commit()
            await db.refresh(conv)

        user_msg = Message(conversation_id=conv.id, role="user", content=f"[Agent] {req.query}")
        db.add(user_msg)
        await db.commit()

        # Load history for context
        hist_result = await db.execute(
            select(Message).where(Message.conversation_id == conv.id).order_by(Message.id)
        )
        history_msgs = hist_result.scalars().all()

    conv_id = conv.id
    is_first = len(history_msgs) <= 1

    # Build initial messages with smart tool routing
    history_for_routing = [{"role": m.role, "content": m.content} for m in history_msgs[-6:]]
    selected_tools = select_tools(req.query, history=history_for_routing)
    system_prompt = build_system_prompt(selected_tools)

    messages = [{"role": "system", "content": system_prompt}]
    for m in history_msgs[-6:]:
        if m.role == "user":
            messages.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            messages.append({"role": "assistant", "content": m.content})

    # Ensure latest user message is there
    if messages[-1]["role"] != "user":
        messages.append({"role": "user", "content": req.query})
    elif messages[-1]["content"] != req.query and not messages[-1]["content"].startswith("[Agent]"):
        messages.append({"role": "user", "content": req.query})

    async def generate():
        all_sources = []
        tool_calls_log = []
        agent_session = AgentSession(conv_id)

        try:
            # Planning step
            yield f"data: {json.dumps({'type': 'agent_thinking', 'round': 0})}\n\n"
            plan = await plan_step(req.query)
            yield f"data: {json.dumps({'type': 'plan', 'message': plan[:300]})}\n\n"

            # Inject plan into agent context
            messages.append({"role": "system", "content": f"Research plan: {plan}"})

            for round_num in range(MAX_TOOL_ROUNDS):
                # Call LLM with tools
                yield f"data: {json.dumps({'type': 'agent_thinking', 'round': round_num + 1})}\n\n"

                choice = await call_llm_with_tools(messages, tools=selected_tools)
                message = choice.get("message", {})

                # Check if LLM wants to call tools
                tool_calls = message.get("tool_calls", [])
                content = message.get("content", "")

                # If there's text content, stream it
                if content:
                    yield f"data: {json.dumps({'type': 'token', 'content': content})}\n\n"

                # If no tool calls, we're done
                if not tool_calls:
                    full_response = content
                    break

                # Add assistant message with tool calls to history
                assistant_msg = {"role": "assistant", "content": content or None, "tool_calls": []}

                for tc in tool_calls:
                    fn = tc.get("function", {})
                    tool_name = fn.get("name", "")
                    tool_args_str = fn.get("arguments", "{}")
                    try:
                        tool_args = json.loads(tool_args_str)
                    except json.JSONDecodeError:
                        tool_args = {}

                    assistant_msg["tool_calls"].append({
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {"name": tool_name, "arguments": tool_args_str},
                    })

                    yield f"data: {json.dumps({'type': 'tool_call', 'tool': tool_name, 'args': tool_args})}\n\n"

                    tool_result = await execute_tool(tool_name, tool_args, session=agent_session)
                    tool_calls_log.append({"tool": tool_name, "args": tool_args, "result_preview": str(tool_result)[:200]})

                    if tool_name == "search" and "results" in tool_result:
                        for s in tool_result["results"]:
                            if s not in all_sources:
                                all_sources.append(s)

                    result_summary = format_tool_result_for_llm(tool_result)[:500]
                    sse_event = {'type': 'tool_result', 'tool': tool_name, 'summary': result_summary}
                    if tool_name == "youtube_play" and tool_result.get("video_id"):
                        sse_event["video_id"] = tool_result["video_id"]
                    if tool_name == "video_play":
                        sse_event["video_type"] = tool_result.get("type", "")
                        sse_event["stream_url"] = tool_result.get("stream_url", "")
                        sse_event["video_id"] = tool_result.get("video_id", "")
                        sse_event["title"] = tool_result.get("title", "")
                    yield f"data: {json.dumps(sse_event)}\n\n"

                    messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": format_tool_result_for_llm(tool_result)})

                messages.append(assistant_msg)
                # Reorder: assistant with tool_calls must come before tool results
                messages_fixed = []
                tool_results_buffer = []
                for m in messages:
                    if m.get("role") == "tool":
                        tool_results_buffer.append(m)
                    else:
                        if tool_results_buffer and m.get("role") == "assistant" and m.get("tool_calls"):
                            messages_fixed.append(m)
                            messages_fixed.extend(tool_results_buffer)
                            tool_results_buffer = []
                        elif tool_results_buffer:
                            messages_fixed.extend(tool_results_buffer)
                            tool_results_buffer = []
                            messages_fixed.append(m)
                        else:
                            messages_fixed.append(m)
                if tool_results_buffer:
                    messages_fixed.extend(tool_results_buffer)
                messages[:] = messages_fixed

                # Reflection every N rounds
                if (round_num + 1) % REFLECT_EVERY_N == 0 and tool_calls_log:
                    reflection = await reflect_step(req.query, round_num + 1, tool_calls_log)
                    yield f"data: {json.dumps({'type': 'reflection', 'message': reflection[:300]})}\n\n"
                    # Inject reflection into agent context
                    messages.append({"role": "system", "content": f"Progress reflection: {reflection}"})

            else:
                full_response = content or "Agent reached maximum tool rounds."
                yield f"data: {json.dumps({'type': 'token', 'content': '\\n\\n*Agent completed after ' + str(MAX_TOOL_ROUNDS) + ' tool rounds.*'})}\n\n"

            # Extract follow-ups
            follow_ups = []
            import re
            fu_match = re.search(r"```followups\s*\n(.*?)\n```", full_response, re.DOTALL)
            if fu_match:
                try:
                    follow_ups = json.loads(fu_match.group(1))
                    full_response = full_response[:fu_match.start()] + full_response[fu_match.end():]
                except json.JSONDecodeError:
                    pass
            full_response = full_response.strip()

            # Save to DB
            async with async_session() as save_db:
                assistant_msg = Message(
                    conversation_id=conv_id,
                    role="assistant",
                    content=full_response,
                    sources=all_sources[:20],
                    follow_ups=follow_ups,
                )
                save_db.add(assistant_msg)
                if is_first:
                    conv_obj = await save_db.get(Conversation, conv_id)
                    if conv_obj:
                        conv_obj.title = f"Agent: {req.query[:80]}"
                await save_db.commit()
                await save_db.refresh(assistant_msg)

                yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv_id, 'message_id': assistant_msg.id, 'sources': all_sources[:20], 'follow_ups': follow_ups, 'tool_calls': len(tool_calls_log), 'title': f'Agent: {req.query[:80]}' if is_first else None})}\n\n"

                # Emit event
                from core.events import emit
                emit("agent.done", {"query": req.query, "conversation_id": conv_id, "tool_calls": len(tool_calls_log)})

        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
