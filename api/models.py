"""Models API — List and refresh LLM models across providers."""

from fastapi import APIRouter, Depends

from core.auth import get_current_user
from core.providers import router as provider_router
from models.user import User

router = APIRouter(tags=["models"])


@router.get("/models")
async def list_models(user: User = Depends(get_current_user)):
    """List all available models across all providers."""
    models = provider_router.list_models()
    # Group by provider
    grouped: dict[str, list[dict]] = {}
    for m in models:
        grouped.setdefault(m["provider"], []).append(m)
    return {"models": models, "providers": grouped, "total": len(models)}


@router.post("/models/refresh")
async def refresh_models(user: User = Depends(get_current_user)):
    """Force refresh model lists from all providers."""
    # Reset cache timestamps to force re-discovery
    for provider in provider_router.providers.values():
        provider.models_fetched_at = 0

    await provider_router.refresh_models()
    models = provider_router.list_models()
    return {"models": models, "total": len(models), "refreshed": True}
