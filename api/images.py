import base64
import logging
import time

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from config import settings
from core.auth import get_current_user
from models.user import User

router = APIRouter(tags=["images"])
logger = logging.getLogger(__name__)


class ImageRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    width: int = 512
    height: int = 512
    steps: int = 20


@router.post("/images/generate")
async def generate_image(req: ImageRequest, user: User = Depends(get_current_user)):
    """Generate an image via Bonsai local image model."""
    try:
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            resp = await client.post(
                f"{settings.bonsai_url}/generate",
                json={
                    "prompt": req.prompt,
                    "width": req.width,
                    "height": req.height,
                    "steps": req.steps,
                },
            )
            resp.raise_for_status()
            wall = time.perf_counter() - t0
            image_b64 = base64.b64encode(resp.content).decode("ascii")
            return {
                "status": "success",
                "image": f"data:image/png;base64,{image_b64}",
                "size_bytes": len(resp.content),
                "wall_seconds": round(wall, 2),
                "width": req.width,
                "height": req.height,
            }
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        return {"error": str(e), "status": "failed"}
