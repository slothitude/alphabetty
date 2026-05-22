import logging
import re
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)


def classify_query(query: str) -> dict:
    """Classify a search query to optimize SearXNG parameters (heuristic, no LLM)."""
    q = query.lower()

    # News / recent
    if any(w in q for w in ("latest", "news", "recent", "today", "this week", "breaking",
                             "update", "current events", "headline")):
        return {"categories": "news", "time_range": "month"}

    # Technical / how-to
    if any(w in q for w in ("how to", "tutorial", "install", "setup", "configure",
                             "guide", "example", "debug", "fix", "error", "issue")):
        return {"categories": "it", "time_range": None}

    # Academic / research
    if any(w in q for w in ("paper", "research", "study", "arxiv", "journal",
                             "publication", "citation", "doi", "survey", "analysis")):
        return {"categories": "science", "time_range": "year"}

    # Science
    if any(w in q for w in ("formula", "equation", "theorem", "proof", "algorithm",
                             "experiment", "hypothesis", "theory")):
        return {"categories": "science", "time_range": None}

    # Files / downloads
    if any(w in q for w in ("download", "github", "release", "binary", "package")):
        return {"categories": "it", "time_range": None}

    # Default
    return {"categories": "general", "time_range": None}


async def search(query: str, categories: str = "general", language: str = "en",
                 max_results: int = 10, time_range: str | None = None) -> list[dict[str, Any]]:
    """Search via SearXNG and return structured results."""
    params = {
        "q": query,
        "format": "json",
        "categories": categories,
        "language": language,
    }
    if time_range:
        params["time_range"] = time_range

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.get(f"{settings.searxng_url}/search", params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"SearXNG search failed: {e}")
        return [{"title": f"Search error: {e}", "url": "", "snippet": str(e), "error": True}]

    results = []
    for item in data.get("results", [])[:max_results]:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
            "engine": item.get("engine", ""),
            "score": item.get("score", 0),
            "category": item.get("category", ""),
        })
    return results


async def adaptive_search(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Classify query and search with appropriate parameters."""
    classification = classify_query(query)
    return await search(
        query,
        categories=classification["categories"],
        max_results=max_results,
        time_range=classification.get("time_range"),
    )


async def search_multiple(queries: list[str], max_per_query: int = 5) -> list[dict[str, Any]]:
    """Run multiple searches and deduplicate results by URL."""
    all_results = []
    seen_urls = set()

    for query in queries:
        results = await adaptive_search(query, max_results=max_per_query)
        for r in results:
            url = r["url"].rstrip("/")
            if url not in seen_urls:
                seen_urls.add(url)
                all_results.append(r)

    return all_results
