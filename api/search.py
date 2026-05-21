from fastapi import APIRouter, Query, Depends
from core.auth import get_current_user
from core.searxng import search
from core.source_citer import format_sources
from models.user import User

router = APIRouter(tags=["search"])


@router.get("/search")
async def search_endpoint(q: str = Query(...), categories: str = Query("general"),
                          max_results: int = Query(10), user: User = Depends(get_current_user)):
    """Search via SearXNG."""
    results = await search(q, categories=categories, max_results=max_results)
    formatted = format_sources(results)
    return {"query": q, "results": formatted}
