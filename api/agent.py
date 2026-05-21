"""Agent mode — Autonomous AI agent with tool calling, planning, and reflection.

The LLM decides when to search, browse, click, extract. It runs in a loop:
1. LLM receives query + tool definitions
2. LLM decides which tool(s) to call (or gives final answer)
3. Tools are executed, results fed back to LLM
4. Repeat until LLM gives a final text answer
5. Stream all steps + final answer to the client via SSE
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import async_session
from core.llm import AGENT_SYSTEM, call_llm, call_llm_with_tools
from core.searxng import adaptive_search as searxng_search
from core.content_extractor import fetch_and_extract
from core.source_citer import format_sources
from models.conversation import Conversation, Message

router = APIRouter(tags=["agent"])
logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 15
REFLECT_EVERY_N = 5


class AgentRequest(BaseModel):
    query: str
    conversation_id: Optional[int] = None
    mode: str = "detailed"


# ─── Planning & Reflection ───

async def plan_step(query: str) -> str:
    """LLM breaks the query into sub-questions and prioritizes tools."""
    messages = [
        {"role": "system", "content": "You are a research planner. Given a query, break it into 2-4 sub-questions and suggest which tools to use first. Be brief — 2-3 sentences max."},
        {"role": "user", "content": f"Plan research for: {query}"},
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

async def execute_tool(name: str, args: dict) -> dict:
    """Execute a tool call and return the result."""
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
            expr = f'document.querySelector("{args["selector"]}")?.innerText || "Element not found"'
            result = await cdp.evaluate(expr)
            return {"tool": "extract", "selector": args["selector"], "text": result or "Not found"}

        elif name == "click":
            from core.cdp_bridge import cdp
            result = await cdp.click(args["selector"])
            return {"tool": "click", "selector": args["selector"], "result": result}

        elif name == "type_text":
            from core.cdp_bridge import cdp
            result = await cdp.type_text(args["selector"], args["text"])
            return {"tool": "type_text", "selector": args["selector"], "result": result}

        elif name == "screenshot":
            from core.cdp_bridge import cdp
            try:
                content = await cdp.get_content()
                return {"tool": "screenshot", "page_text_preview": content[:2000]}
            except Exception as e:
                return {"tool": "screenshot", "error": str(e)}

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

    return json.dumps(tool_result)


@router.post("/agent")
async def agent_chat(req: AgentRequest):
    """Autonomous agent: LLM decides tools to call, executes them, loops until answer."""

    # Get or create conversation
    conv = None
    async with async_session() as db:
        if req.conversation_id:
            result = await db.execute(select(Conversation).where(Conversation.id == req.conversation_id))
            conv = result.scalar_one_or_none()
        if not conv:
            conv = Conversation(title=f"Agent: {req.query[:80]}", mode=req.mode)
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

    # Build initial messages
    messages = [{"role": "system", "content": AGENT_SYSTEM}]
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

                choice = await call_llm_with_tools(messages)
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

                    tool_result = await execute_tool(tool_name, tool_args)
                    tool_calls_log.append({"tool": tool_name, "args": tool_args, "result_preview": str(tool_result)[:200]})

                    if tool_name == "search" and "results" in tool_result:
                        for s in tool_result["results"]:
                            if s not in all_sources:
                                all_sources.append(s)

                    result_summary = format_tool_result_for_llm(tool_result)[:500]
                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': tool_name, 'summary': result_summary})}\n\n"

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

        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
