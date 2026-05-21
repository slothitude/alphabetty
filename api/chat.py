import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import async_session
from core.llm import stream_llm, call_llm, _build_messages
from core.searxng import adaptive_search
from core.source_citer import format_sources
from models.conversation import Conversation, Message

router = APIRouter(tags=["chat"])


async def get_db():
    async with async_session() as session:
        yield session


class ChatRequest(BaseModel):
    query: str
    conversation_id: Optional[int] = None
    mode: str = "concise"  # concise, detailed, creative, academic, code
    search_enabled: bool = True


class ConversationCreate(BaseModel):
    title: str = "New Chat"
    mode: str = "concise"


class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    mode: Optional[str] = None


@router.post("/conversations")
async def create_conversation(data: ConversationCreate, db: AsyncSession = Depends(get_db)):
    conv = Conversation(title=data.title, mode=data.mode)
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return {"id": conv.id, "title": conv.title, "mode": conv.mode}


@router.get("/conversations")
async def list_conversations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conversation).order_by(Conversation.updated_at.desc())
    )
    convs = result.scalars().all()
    out = []
    for c in convs:
        # Get preview (first 100 chars of first assistant reply)
        preview = ""
        first_assistant = await db.execute(
            select(Message).where(
                Message.conversation_id == c.id,
                Message.role == "assistant",
            ).order_by(Message.id).limit(1)
        )
        msg = first_assistant.scalar_one_or_none()
        if msg and msg.content:
            preview = msg.content[:100].replace("\n", " ")

        # Get tags
        from models.graph import Tag, ConversationTag
        tags_result = await db.execute(
            select(Tag).join(ConversationTag, ConversationTag.tag_id == Tag.id)
            .where(ConversationTag.conversation_id == c.id)
        )
        tags = [{"id": t.id, "name": t.name, "color": t.color} for t in tags_result.scalars().all()]

        out.append({
            "id": c.id,
            "title": c.title,
            "mode": c.mode,
            "updated_at": c.updated_at.isoformat(),
            "preview": preview,
            "tags": tags,
        })
    return out


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conversation).where(Conversation.id == conv_id)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        return {"error": "Not found"}, 404

    msgs_result = await db.execute(
        select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
    )
    messages = msgs_result.scalars().all()

    return {
        "id": conv.id,
        "title": conv.title,
        "mode": conv.mode,
        "tags": await _get_conv_tags(conv.id, db),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "sources": m.sources or [],
                "follow_ups": m.follow_ups or [],
            }
            for m in messages
        ],
    }


async def _get_conv_tags(conv_id: int, db: AsyncSession):
    from models.graph import Tag, ConversationTag
    result = await db.execute(
        select(Tag).join(ConversationTag, ConversationTag.tag_id == Tag.id)
        .where(ConversationTag.conversation_id == conv_id)
    )
    return [{"id": t.id, "name": t.name, "color": t.color} for t in result.scalars().all()]


@router.patch("/conversations/{conv_id}")
async def update_conversation(conv_id: int, data: ConversationUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        return {"error": "Not found"}
    if data.title:
        conv.title = data.title
    if data.mode:
        conv.mode = data.mode
    await db.commit()
    return {"id": conv.id, "title": conv.title, "mode": conv.mode}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if conv:
        await db.delete(conv)
        await db.commit()
    return {"ok": True}


@router.post("/chat")
async def chat(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    """Send a message and get an SSE-streamed AI response with optional search."""
    # Get or create conversation
    if req.conversation_id:
        result = await db.execute(select(Conversation).where(Conversation.id == req.conversation_id))
        conv = result.scalar_one_or_none()
        if not conv:
            conv = Conversation(title="New Chat", mode=req.mode)
            db.add(conv)
            await db.commit()
            await db.refresh(conv)
    else:
        conv = Conversation(title=req.query[:100], mode=req.mode)
        db.add(conv)
        await db.commit()
        await db.refresh(conv)

    # Save user message
    user_msg = Message(conversation_id=conv.id, role="user", content=req.query)
    db.add(user_msg)
    await db.commit()

    # Load history
    hist_result = await db.execute(
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.id)
    )
    history_msgs = hist_result.scalars().all()
    history = [{"role": m.role, "content": m.content} for m in history_msgs]

    # Search for sources if enabled
    sources = []
    search_results = []
    if req.search_enabled:
        search_results = await adaptive_search(req.query, max_results=8)
        sources = format_sources(search_results, query=req.query)

    # Build messages
    messages = await _build_messages(req.query, sources=search_results if req.search_enabled else None,
                               mode=conv.mode, history=history[:-1], conversation_id=conv.id)

    # Auto-title if first message
    is_first = len(history_msgs) <= 1

    async def generate():
        full_response = ""
        try:
            async for chunk in stream_llm(messages):
                full_response += chunk
                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"

            # Extract follow-ups
            follow_ups = []
            fu_match = re.search(r"```followups\s*\n(.*?)\n```", full_response, re.DOTALL)
            if fu_match:
                try:
                    follow_ups = json.loads(fu_match.group(1))
                    full_response = full_response[:fu_match.start()] + full_response[fu_match.end():]
                except json.JSONDecodeError:
                    pass

            # Clean up the response
            full_response = full_response.strip()

            # Save assistant message
            async with async_session() as save_db:
                assistant_msg = Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content=full_response,
                    sources=sources,
                    follow_ups=follow_ups,
                )
                save_db.add(assistant_msg)

                # Auto-title if first message
                if is_first:
                    conv_obj = await save_db.get(Conversation, conv.id)
                    if conv_obj:
                        conv_obj.title = req.query[:100]

                await save_db.commit()
                await save_db.refresh(assistant_msg)

                # Extract entities + update domain graph (fire-and-forget)
                try:
                    from core.graph import extract_and_store_entities, update_domain_graph
                    await extract_and_store_entities(assistant_msg.id, full_response)
                    await extract_and_store_entities(user_msg.id, req.query)
                    if sources:
                        await update_domain_graph(sources)
                except Exception as e:
                    pass  # Non-critical

                yield f"data: {json.dumps({'type': 'done', 'message_id': assistant_msg.id, 'conversation_id': conv.id, 'sources': sources, 'follow_ups': follow_ups, 'title': req.query[:100] if is_first else None})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
