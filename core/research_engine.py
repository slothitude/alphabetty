"""Deep Research Engine — Multi-step research with progress reporting."""

import json
import logging
from typing import AsyncIterator

from core.llm import call_llm
from core.searxng import search_multiple, adaptive_search
from core.content_extractor import fetch_and_extract
from core.source_citer import format_sources

logger = logging.getLogger(__name__)


async def plan_queries(query: str) -> list[str]:
    """Generate diverse search queries from a research question."""
    messages = [
        {"role": "system", "content": "Generate 3-5 diverse search queries that together would comprehensively cover this topic. Return ONLY a JSON array of query strings."},
        {"role": "user", "content": query},
    ]
    result = await call_llm(messages)
    try:
        cleaned = result.strip().strip("```json").strip().strip("`")
        queries = json.loads(cleaned)
        if isinstance(queries, str):
            queries = json.loads(queries)
        return queries if isinstance(queries, list) else [query]
    except json.JSONDecodeError:
        return [query]


async def find_gaps(query: str, extracted: list[dict]) -> list[dict]:
    """Identify information gaps and generate follow-up queries with priority scores.

    Returns list of {"query": str, "priority": float, "aspect": str} sorted by priority.
    """
    sources_summary = json.dumps([
        {"title": e["title"], "url": e["url"], "snippet": e.get("snippet", "")[:100]}
        for e in extracted[:15]
    ])
    messages = [
        {
            "role": "system",
            "content": """Analyze the research collected. Identify specific gaps needing more investigation.
Return ONLY a JSON array of objects:
[{"query": "search query string", "priority": 0.0-1.0, "aspect": "brief description of what's missing"}]

Priority: 1.0 = critical gap, 0.0 = nice to have. Max 5 items.""",
        },
        {
            "role": "user",
            "content": f"Original query: {query}\n\nSources found ({len(extracted)} total):\n{sources_summary}",
        },
    ]
    result = await call_llm(messages)
    try:
        cleaned = result.strip().strip("```json").strip().strip("`")
        gaps = json.loads(cleaned)
        if isinstance(gaps, list):
            # Validate and sort by priority
            valid_gaps = []
            for g in gaps:
                if isinstance(g, dict) and g.get("query"):
                    valid_gaps.append({
                        "query": g["query"],
                        "priority": min(float(g.get("priority", 0.5)), 1.0),
                        "aspect": g.get("aspect", ""),
                    })
                elif isinstance(g, str):
                    valid_gaps.append({"query": g, "priority": 0.5, "aspect": ""})
            valid_gaps.sort(key=lambda x: x["priority"], reverse=True)
            return valid_gaps[:5]
    except (json.JSONDecodeError, ValueError) as e:
        logger.debug(f"Gap analysis parse failed: {e}")
    return []


async def deep_research(query: str, depth: int = 3, mode: str = "detailed") -> AsyncIterator[dict]:
    """Full deep research pipeline with progress events."""
    # Phase 1: Plan
    yield {"type": "progress", "step": "planning", "message": "Planning research strategy..."}

    queries = await plan_queries(query)
    yield {"type": "progress", "step": "queries", "message": f"Generated {len(queries)} search queries", "queries": queries}

    # Phase 2: Search
    yield {"type": "progress", "step": "searching", "message": f"Searching {len(queries)} queries..."}

    all_results = await search_multiple(queries, max_per_query=5)
    yield {"type": "progress", "step": "found", "message": f"Found {len(all_results)} unique sources", "count": len(all_results)}

    # Phase 3: Extract content
    yield {"type": "progress", "step": "reading", "message": "Reading and extracting content..."}

    extracted = []
    seen_urls = set()
    for i, result in enumerate(all_results[:10]):
        url = result.get("url", "").rstrip("/")
        if url in seen_urls:
            continue
        seen_urls.add(url)

        yield {"type": "progress", "step": "extracting", "message": f"Reading {i+1}/{min(len(all_results), 10)}: {result.get('title', '')[:60]}..."}

        content = await fetch_and_extract(result["url"])
        if content.get("text"):
            extracted.append({
                "title": content.get("title", result.get("title", "")),
                "url": result["url"],
                "text": content["text"][:8000],
                "snippet": result.get("snippet", ""),
            })

    # Phase 4: Gap analysis (additional rounds) with convergence check
    for round_num in range(1, depth):
        if not extracted:
            break

        yield {"type": "progress", "step": "analyzing", "message": f"Analyzing gaps (round {round_num + 1})..."}

        gap_items = await find_gaps(query, extracted)
        if not gap_items:
            break

        # Only execute top 3 gap queries (avoid diminishing returns)
        gap_queries = [g["query"] for g in gap_items[:3]]
        aspects = "; ".join(g.get("aspect", "") for g in gap_items[:3])

        yield {"type": "progress", "step": "gap_search", "message": f"Searching {len(gap_queries)} gap-filling queries: {aspects[:100]}"}

        gap_results = await search_multiple(gap_queries, max_per_query=3)

        # Convergence check: if >50% of URLs already seen, stop early
        new_urls = [r["url"].rstrip("/") for r in gap_results]
        overlap_count = sum(1 for u in new_urls if u in seen_urls)
        if new_urls and overlap_count / len(new_urls) > 0.5:
            yield {"type": "progress", "step": "converged", "message": f"Convergence reached ({overlap_count}/{len(new_urls)} sources already seen). Stopping early."}
            break

        for gr in gap_results[:5]:
            url = gr.get("url", "").rstrip("/")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            content = await fetch_and_extract(gr["url"])
            if content.get("text"):
                extracted.append({
                    "title": content.get("title", gr.get("title", "")),
                    "url": gr["url"],
                    "text": content["text"][:8000],
                    "snippet": gr.get("snippet", ""),
                })

    # Phase 5: Synthesize
    yield {"type": "progress", "step": "synthesizing", "message": f"Synthesizing {len(extracted)} sources..."}

    return extracted
