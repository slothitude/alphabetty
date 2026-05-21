import io
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import get_current_user, get_db
from models.conversation import Conversation, Message
from models.user import User

router = APIRouter(tags=["export"])


@router.get("/export/markdown/{conv_id}")
async def export_markdown(conv_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        return {"error": "Not found"}

    msgs = await db.execute(
        select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
    )
    messages = msgs.scalars().all()

    lines = [f"# {conv.title}\n", f"Exported: {datetime.now(timezone.utc).isoformat()}\n\n---\n"]

    for m in messages:
        role_label = "**You**" if m.role == "user" else "**Alphabetty**"
        lines.append(f"\n### {role_label}\n\n{m.content}\n")

        if m.sources:
            lines.append("\n**Sources:**\n")
            for s in m.sources:
                idx = s.get("index", "?")
                lines.append(f"- [{idx}] [{s.get('title', 'Untitled')}]({s.get('url', '')}) — {s.get('domain', '')}")

        if m.follow_ups:
            lines.append("\n**Follow-up questions:**\n")
            for fq in m.follow_ups:
                lines.append(f"- {fq}")

        lines.append("\n---\n")

    md = "\n".join(lines)
    filename = re.sub(r'[^\w\s-]', '', conv.title)[:50].strip().replace(' ', '_')
    return Response(
        content=md,
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={filename}.md"},
    )


@router.get("/export/pdf/{conv_id}")
async def export_pdf(conv_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        return {"error": "Not found"}

    msgs = await db.execute(
        select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
    )
    messages = msgs.scalars().all()

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.units import inch

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.75*inch, bottomMargin=0.75*inch)
    styles = getSampleStyleSheet()
    story = []

    # Title
    title_style = ParagraphStyle("ConvTitle", parent=styles["Title"], fontSize=18)
    story.append(Paragraph(conv.title, title_style))
    story.append(Spacer(1, 0.2 * inch))
    story.append(HRFlowable(width="100%"))
    story.append(Spacer(1, 0.2 * inch))

    for m in messages:
        role = "You" if m.role == "user" else "Alphabetty"
        role_style = ParagraphStyle("Role", parent=styles["Heading2"], fontSize=13)
        story.append(Paragraph(role, role_style))

        # Escape HTML special chars for reportlab
        content = m.content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        content = content.replace("\n", "<br/>")
        body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=14)
        story.append(Paragraph(content, body_style))

        if m.sources:
            story.append(Spacer(1, 0.1 * inch))
            sources_text = "Sources: " + ", ".join(
                f"[{s.get('index', '?')}] {s.get('title', '')}" for s in m.sources
            )
            src_style = ParagraphStyle("Sources", parent=styles["Normal"], fontSize=8, textColor="gray")
            story.append(Paragraph(sources_text.replace("&", "&amp;").replace("<", "&lt;"), src_style))

        story.append(Spacer(1, 0.2 * inch))
        story.append(HRFlowable(width="80%", color="lightgray"))
        story.append(Spacer(1, 0.1 * inch))

    doc.build(story)
    buffer.seek(0)

    filename = re.sub(r'[^\w\s-]', '', conv.title)[:50].strip().replace(' ', '_')
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}.pdf"},
    )
