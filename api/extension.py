"""Extension endpoints — health check, page capture, context retrieval."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import get_current_user, get_db
from models.user import User

router = APIRouter(prefix="/ext", tags=["extension"])

VERSION = "0.1.0"


@router.get("/health")
async def extension_health(user: User = Depends(get_current_user)):
    """Validate extension connection. Auth via API key (Bearer header)."""
    return {
        "version": VERSION,
        "user": user.username,
        "status": "ok",
    }
