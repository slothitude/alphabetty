import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import async_session
from core.llm import call_llm, _build_messages
from core.searxng import search_multiple
from core.content_extractor import fetch_and_extract
from core.source_citer import format_sources
from models.conversation import Conversation, Message

router = APIRouter(tags=["research"])
logger = logging.getLogger(__name__)


class ResearchRequest(BaseModel):
    query: str
    conversation_id: Optional[int] = None
    depth: int = 3  # number of research rounds
    mode: str = "detailed"


@router.post("/research")
async def deep_research(req: ResearchRequest):
    """Deep research: plan → search → extract → gap analysis → synthesize."""
    async def generate():
        try:
            # Step 1: Plan research queries
            yield f"data: {json.dumps({'type': 'progress', 'step': 'planning', 'message': 'Planning research strategy...'})}\n\n"

            plan_prompt = [
                {"role": "system", "content": "You are a research planner. Given a query, generate 3-5 diverse search queries that together would comprehensively cover the topic. Return ONLY a JSON array of query strings, no explanation."},
                {"role": "user", "content": req.query},
            ]
            plan_result = await call_llm(plan_prompt)

            # Parse queries
            try:
                queries = json.loads(plan_result.strip().strip("```json").strip().strip("`"))
                if isinstance(queries, str):
                    queries = json.loads(queries)
            except json.JSONDecodeError:
                queries = [req.query]

            yield f"data: {json.dumps({'type': 'progress', 'step': 'queries', 'message': f'Generated {len(queries)} search queries', 'queries': queries})}\n\n"

            # Step 2: Execute searches
            yield f"data: {json.dumps({'type': 'progress', 'step': 'searching', 'message': f'Searching {len(queries)} queries...'})}\n\n"

            all_results = await search_multiple(queries, max_per_query=5)
            yield f"data: {json.dumps({'type': 'progress', 'step': 'found', 'message': f'Found {len(all_results)} sources', 'count': len(all_results)})}\n\n"

            # Step 3: Extract content from top sources
            yield f"data: {json.dumps({'type': 'progress', 'step': 'reading', 'message': 'Reading and extracting content...'})}\n\n"

            extracted = []
            for i, result in enumerate(all_results[:10]):
                title_preview = result.get("title", "")[:60]
                yield f"data: {json.dumps({'type': 'progress', 'step': 'extracting', 'message': f'Reading source {i+1}/{min(len(all_results), 10)}: {title_preview}...'})}\n\n"
                content = await fetch_and_extract(result["url"])
                if content.get("text"):
                    extracted.append({
                        "title": content.get("title", result.get("title", "")),
                        "url": result["url"],
                        "text": content["text"][:8000],
                        "snippet": result.get("snippet", ""),
                    })

            # Step 4: Additional round if depth > 1
            if req.depth > 1 and extracted:
                yield f"data: {json.dumps({'type': 'progress', 'step': 'analyzing', 'message': 'Analyzing gaps in coverage...'})}\n\n"

                gap_prompt = [
                    {"role": "system", "content": "Analyze the research collected so far. Identify 2-3 specific gaps or questions that need more investigation. Return ONLY a JSON array of search query strings."},
                    {"role": "user", "content": f"Original query: {req.query}\n\nSources found: {json.dumps([{'title': e['title'], 'url': e['url']} for e in extracted])}"},
                ]
                gap_result = await call_llm(gap_prompt)
                try:
                    gap_queries = json.loads(gap_result.strip().strip("```json").strip().strip("`"))
                    if isinstance(gap_queries, list) and gap_queries:
                        yield f"data: {json.dumps({'type': 'progress', 'step': 'gap_search', 'message': f'Searching {len(gap_queries)} gap-filling queries...'})}\n\n"
                        gap_results = await search_multiple(gap_queries, max_per_query=3)
                        for gr in gap_results[:5]:
                            content = await fetch_and_extract(gr["url"])
                            if content.get("text"):
                                extracted.append({
                                    "title": content.get("title", gr.get("title", "")),
                                    "url": gr["url"],
                                    "text": content["text"][:8000],
                                    "snippet": gr.get("snippet", ""),
                                })
                except json.JSONDecodeError:
                    pass

            # Step 5: Synthesize
            yield f"data: {json.dumps({'type': 'progress', 'step': 'synthesizing', 'message': f'Synthesizing {len(extracted)} sources into report...'})}\n\n"

            sources_text = "\n\n".join(
                f"[Source {i+1}] {e['title']}\nURL: {e['url']}\n{e['text'][:4000]}"
                for i, e in enumerate(extracted)
            )

            synth_messages = await _build_messages(
                f"Based on the following sources, provide a comprehensive answer to: {req.query}\n\n"
                f"Sources:\n{sources_text}",
                sources=None,
                mode=req.mode,
                conversation_id=req.conversation_id,
            )

            full_response = ""
            from core.llm import stream_llm
            async for chunk in stream_llm(synth_messages):
                full_response += chunk
                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"

            # Save to conversation
            sources_formatted = format_sources([{"title": e["title"], "url": e["url"], "snippet": e["snippet"]} for e in extracted], query=req.query)

            async with async_session() as db:
                if req.conversation_id:
                    conv = await db.get(Conversation, req.conversation_id)
                if not req.conversation_id or not conv:
                    conv = Conversation(title=f"Research: {req.query[:80]}", mode=req.mode)
                    db.add(conv)
                    await db.commit()
                    await db.refresh(conv)

                user_msg = Message(conversation_id=conv.id, role="user", content=f"[Research] {req.query}")
                db.add(user_msg)
                await db.commit()

                assistant_msg = Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content=full_response,
                    sources=sources_formatted,
                )
                db.add(assistant_msg)
                await db.commit()
                await db.refresh(assistant_msg)

                yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv.id, 'message_id': assistant_msg.id, 'sources': sources_formatted})}\n\n"

        except Exception as e:
            logger.error(f"Research error: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
