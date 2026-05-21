import logging

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from config import settings

router = APIRouter(tags=["images"])
logger = logging.getLogger(__name__)


class ImageRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1024
    steps: int = 20


@router.post("/images/generate")
async def generate_image(req: ImageRequest):
    """Generate an image via Smart Router → ComfyUI."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            resp = await client.post(
                f"{settings.router_url}/api/generate",
                json={
                    "prompt": req.prompt,
                    "negative_prompt": req.negative_prompt,
                    "width": req.width,
                    "height": req.height,
                    "steps": req.steps,
                },
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        return {"error": str(e), "status": "failed"}
