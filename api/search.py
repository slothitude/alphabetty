from fastapi import APIRouter, Query
from core.searxng import search
from core.source_citer import format_sources

router = APIRouter(tags=["search"])


@router.get("/search")
async def search_endpoint(q: str = Query(...), categories: str = Query("general"),
                          max_results: int = Query(10)):
    """Search via SearXNG."""
    results = await search(q, categories=categories, max_results=max_results)
    formatted = format_sources(results)
    return {"query": q, "results": formatted}
