"""Conversation sharing — create and view expiring read-only links."""

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import get_current_user, get_db
from models.conversation import Conversation, Message
from models.share import Share
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["share"])

DEFAULT_TTL_HOURS = 72


@router.post("/share/{conv_id}")
async def create_share(
    conv_id: int,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a public, read-only, expiring link to a conversation."""
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        return {"error": "Conversation not found"}

    share = Share(
        conversation_id=conv_id,
        created_by=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl_hours),
    )
    db.add(share)
    await db.commit()
    await db.refresh(share)

    return {
        "token": share.token,
        "url": f"/s/{share.token}",
        "expires_at": share.expires_at.isoformat(),
        "ttl_hours": ttl_hours,
    }


@router.get("/share/{conv_id}/links")
async def list_shares(
    conv_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all active share links for a conversation."""
    result = await db.execute(
        select(Share)
        .where(Share.conversation_id == conv_id, Share.created_by == user.id)
        .order_by(Share.created_at.desc())
    )
    shares = result.scalars().all()
    now = datetime.now(timezone.utc)
    return {
        "shares": [
            {
                "token": s.token,
                "url": f"/s/{s.token}",
                "expires_at": s.expires_at.isoformat(),
                "expired": s.expires_at < now if s.expires_at else False,
                "created_at": s.created_at.isoformat(),
            }
            for s in shares
        ]
    }


@router.delete("/share/{token}")
async def revoke_share(
    token: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a share link."""
    result = await db.execute(select(Share).where(Share.token == token))
    share = result.scalar_one_or_none()
    if not share:
        return {"error": "Share not found"}
    if share.created_by != user.id:
        return {"error": "Not authorized"}
    await db.delete(share)
    await db.commit()
    return {"ok": True, "revoked": token}


@router.get("/s/{token}", response_class=HTMLResponse)
async def view_share(token: str, db: AsyncSession = Depends(get_db)):
    """Public shared conversation view — no auth required."""
    result = await db.execute(select(Share).where(Share.token == token))
    share = result.scalar_one_or_none()
    if not share:
        return HTMLResponse("<h1>Not found</h1><p>This share link doesn't exist.</p>", status_code=404)

    now = datetime.now(timezone.utc)
    if share.expires_at and share.expires_at < now:
        return HTMLResponse("<h1>Expired</h1><p>This share link has expired.</p>", status_code=410)

    conv = share.conversation
    msgs_result = await db.execute(
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.id)
    )
    messages = msgs_result.scalars().all()

    # Render as simple HTML
    rows = ""
    for m in messages:
        role = "You" if m.role == "user" else "Alphabetty"
        role_class = "user" if m.role == "user" else "assistant"
        content = (m.content or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        content = content.replace("\n", "<br>")
        rows += f'<div class="msg {role_class}"><strong>{role}</strong><br>{content}</div>'

        if m.sources:
            rows += '<div class="sources"><em>Sources:</em> '
            rows += ", ".join(f'[{s.get("index","?")}] {s.get("title","")}' for s in m.sources)
            rows += "</div>"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{conv.title}</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #0f0f0f; color: #e0e0e0; }}
h1 {{ border-bottom: 1px solid #333; padding-bottom: 10px; }}
.msg {{ padding: 12px 16px; margin: 8px 0; border-radius: 8px; }}
.user {{ background: #1a2a1a; }}
.assistant {{ background: #1a1a2a; }}
.sources {{ padding: 4px 16px; font-size: 0.85em; color: #888; }}
.expires {{ font-size: 0.8em; color: #666; margin-top: 20px; }}
</style></head><body>
<h1>{conv.title}</h1>
{rows}
<div class="expires">Shared conversation — expires {share.expires_at.strftime('%Y-%m-%d %H:%M UTC') if share.expires_at else 'never'}</div>
</body></html>"""
    return HTMLResponse(html)
