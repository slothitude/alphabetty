from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import async_session
from models.conversation import Conversation
from models.space import Space

router = APIRouter(tags=["spaces"])


async def get_db():
    async with async_session() as session:
        yield session


class SpaceCreate(BaseModel):
    name: str
    description: str = ""
    color: str = "#3b82f6"


class SpaceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None


@router.post("/spaces")
async def create_space(data: SpaceCreate, db: AsyncSession = Depends(get_db)):
    space = Space(name=data.name, description=data.description, color=data.color)
    db.add(space)
    await db.commit()
    await db.refresh(space)
    return {"id": space.id, "name": space.name, "description": space.description, "color": space.color}


@router.get("/spaces")
async def list_spaces(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Space).order_by(Space.created_at.desc()))
    spaces = result.scalars().all()
    out = []
    for s in spaces:
        convs = await db.execute(
            select(Conversation).where(Conversation.space_id == s.id)
        )
        conv_count = len(convs.scalars().all())
        out.append({
            "id": s.id, "name": s.name, "description": s.description,
            "color": s.color, "conversation_count": conv_count,
        })
    return out


@router.get("/spaces/{space_id}")
async def get_space(space_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Space).where(Space.id == space_id))
    space = result.scalar_one_or_none()
    if not space:
        return {"error": "Not found"}
    convs = await db.execute(
        select(Conversation).where(Conversation.space_id == space_id).order_by(Conversation.updated_at.desc())
    )
    conversations = convs.scalars().all()
    return {
        "id": space.id, "name": space.name, "description": space.description, "color": space.color,
        "conversations": [
            {"id": c.id, "title": c.title, "mode": c.mode, "updated_at": c.updated_at.isoformat()}
            for c in conversations
        ],
    }


@router.patch("/spaces/{space_id}")
async def update_space(space_id: int, data: SpaceUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Space).where(Space.id == space_id))
    space = result.scalar_one_or_none()
    if not space:
        return {"error": "Not found"}
    if data.name:
        space.name = data.name
    if data.description is not None:
        space.description = data.description
    if data.color:
        space.color = data.color
    await db.commit()
    return {"id": space.id, "name": space.name, "description": space.description, "color": space.color}


@router.delete("/spaces/{space_id}")
async def delete_space(space_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Space).where(Space.id == space_id))
    space = result.scalar_one_or_none()
    if space:
        # Unlink conversations
        convs = await db.execute(
            select(Conversation).where(Conversation.space_id == space_id)
        )
        for c in convs.scalars().all():
            c.space_id = None
        await db.delete(space)
        await db.commit()
    return {"ok": True}


@router.post("/spaces/{space_id}/add/{conv_id}")
async def add_to_space(space_id: int, conv_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if conv:
        conv.space_id = space_id
        await db.commit()
    return {"ok": True}
