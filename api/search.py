import asyncio
import logging

from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel
from core.auth import get_current_user
from core.searxng import search, adaptive_search
from core.content_extractor import fetch_and_extract
from core.source_citer import format_sources
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["search"])


@router.get("/search")
async def search_endpoint(q: str = Query(...), categories: str = Query("general"),
                          max_results: int = Query(10), user: User = Depends(get_current_user)):
    """Search via SearXNG."""
    results = await search(q, categories=categories, max_results=max_results)
    formatted = format_sources(results)
    return {"query": q, "results": formatted}


@router.get("/read-url")
async def read_url_endpoint(url: str = Query(...), max_length: int = Query(50000),
                            format: str = Query("text"),
                            include_links: bool = Query(False),
                            user: User = Depends(get_current_user)):
    """Fetch a URL and return clean extracted text. Fast HTTP fetch, no browser needed.

    Args:
        url: URL to fetch
        max_length: Max text length (default 50000)
        format: Output format — "text" (plain) or "markdown" (preserve headings/links)
        include_links: Include a links summary section at the end
    """
    result = await fetch_and_extract(url, max_length=max_length)

    if format == "markdown" or include_links:
        result = await _enrich_result(result, url, include_links, format)

    return result


async def _enrich_result(result: dict, url: str, include_links: bool, fmt: str) -> dict:
    """Add markdown formatting and/or link summary to extracted content."""
    import httpx as _hx
    from bs4 import BeautifulSoup

    text = result.get("text", "")

    if include_links:
        try:
            async with _hx.AsyncClient(timeout=_hx.Timeout(15.0), follow_redirects=True,
                                       headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")
                links = []
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    label = a.get_text(strip=True)[:100]
                    if label and href.startswith("http"):
                        links.append({"text": label, "url": href})
                if links:
                    result["links"] = links[:30]
                    result["links_summary"] = "\n".join(f"- [{l['text']}]({l['url']})" for l in links[:30])
        except Exception:
            pass

    if fmt == "markdown" and text:
        lines = text.split("\n")
        md_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and len(stripped) < 80 and not stripped.endswith((".", ",", ":", ";")):
                md_lines.append(f"### {stripped}")
            else:
                md_lines.append(line)
        result["text"] = "\n".join(md_lines)

    return result


class SearchAndReadRequest(BaseModel):
    query: str
    max_results: int = 3
    max_length_per_page: int = 8000


@router.post("/search-and-read")
async def search_and_read_endpoint(req: SearchAndReadRequest,
                                   user: User = Depends(get_current_user)):
    """Search and read top results in one call. Returns combined clean text."""
    results = await adaptive_search(req.query, max_results=req.max_results)

    if not results or results[0].get("error"):
        return {"query": req.query, "results": [], "pages": [], "combined_text": ""}

    # Fetch top N pages in parallel
    urls = [r["url"] for r in results if r.get("url")]
    pages = await asyncio.gather(
        *[fetch_and_extract(u, max_length=req.max_length_per_page) for u in urls],
        return_exceptions=True,
    )

    page_data = []
    text_parts = []
    for i, page in enumerate(pages):
        if isinstance(page, Exception):
            page_data.append({"url": urls[i], "title": "", "text": "", "error": str(page)})
            continue
        page_data.append(page)
        if page.get("text"):
            text_parts.append(f"## {page.get('title', 'Untitled')}\nSource: {page['url']}\n\n{page['text']}")

    return {
        "query": req.query,
        "results": [{"title": r["title"], "url": r["url"], "snippet": r.get("snippet", "")} for r in results],
        "pages": page_data,
        "combined_text": "\n\n---\n\n".join(text_parts),
    }
