"""Source citation formatting — footnote numbering, formatting, and quality scoring."""

import re
from urllib.parse import urlparse


# Domain authority scores for known high-quality sources (0.0-1.0)
DOMAIN_AUTHORITY = {
    # Academic / Research
    "wikipedia.org": 0.90, "wikimedia.org": 0.85,
    "arxiv.org": 0.92, "doi.org": 0.88, "semanticscholar.org": 0.88,
    "scholar.google.com": 0.87, "nature.com": 0.93, "science.org": 0.93,
    "ieee.org": 0.85, "acm.org": 0.85, "springer.com": 0.87,
    "pubmed.ncbi.nlm.nih.gov": 0.90, "ncbi.nlm.nih.gov": 0.88,
    "plos.org": 0.85, "jstor.org": 0.86,
    # Tech / Developer
    "github.com": 0.85, "stackoverflow.com": 0.80, "stackexchange.com": 0.78,
    "docs.python.org": 0.90, "developer.mozilla.org": 0.90,
    "mdn.io": 0.88, "readthedocs.io": 0.82, "docs.rs": 0.85,
    "npmjs.com": 0.78, "pypi.org": 0.80, "crates.io": 0.78,
    "microsoft.com": 0.80, "azure.microsoft.com": 0.82,
    "aws.amazon.com": 0.83, "cloud.google.com": 0.83,
    "openai.com": 0.82, "anthropic.com": 0.82, "deepmind.com": 0.82,
    # News / Media
    "reuters.com": 0.85, "apnews.com": 0.85, "bbc.com": 0.83, "bbc.co.uk": 0.83,
    "nytimes.com": 0.82, "theguardian.com": 0.81, "washingtonpost.com": 0.81,
    "economist.com": 0.83, "bloomberg.com": 0.82, "ft.com": 0.82,
    # Reference
    "britannica.com": 0.85, "merriam-webster.com": 0.82,
    # General
    "medium.com": 0.65, "towardsdatascience.com": 0.70,
    "reddit.com": 0.60, "quora.com": 0.55,
    "youtube.com": 0.55, "w3schools.com": 0.65,
    "geeksforgeeks.org": 0.60, "tutorialspoint.com": 0.55,
}


def extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.netloc.replace("www.", "")
    except Exception:
        return url


def get_domain_authority(domain: str) -> float:
    """Get authority score for a domain, with subdomain matching."""
    if domain in DOMAIN_AUTHORITY:
        return DOMAIN_AUTHORITY[domain]
    # Check parent domain (e.g. en.wikipedia.org → wikipedia.org)
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        parent = ".".join(parts[i:])
        if parent in DOMAIN_AUTHORITY:
            return DOMAIN_AUTHORITY[parent]
    return 0.50  # default for unknown domains


def score_source(source: dict, query: str = "") -> float:
    """Score a source 0.0-1.0 based on domain authority + keyword overlap."""
    domain = extract_domain(source.get("url", ""))
    authority = get_domain_authority(domain)

    # Keyword overlap bonus (0.0-0.15)
    overlap = 0.0
    if query:
        query_words = set(re.findall(r"\w+", query.lower()))
        title_words = set(re.findall(r"\w+", source.get("title", "").lower()))
        snippet_words = set(re.findall(r"\w+", source.get("snippet", "").lower()))
        source_words = title_words | snippet_words
        if query_words and source_words:
            overlap = min(len(query_words & source_words) / max(len(query_words), 1), 1.0) * 0.15

    return min(authority + overlap, 1.0)


def rank_sources(sources: list[dict], query: str = "") -> list[dict]:
    """Rank sources by quality score, return sorted list."""
    scored = [(s, score_source(s, query)) for s in sources]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in scored]


def format_sources(sources: list[dict], query: str = "") -> list[dict]:
    """Rank sources, add index, domain, score, and authority to source dicts."""
    ranked = rank_sources(sources, query) if query else sources
    formatted = []
    for i, s in enumerate(ranked):
        domain = extract_domain(s.get("url", ""))
        formatted.append({
            "index": i + 1,
            "title": s.get("title", "Untitled"),
            "url": s.get("url", ""),
            "snippet": s.get("snippet", ""),
            "domain": domain,
            "score": round(score_source(s, query), 2),
            "authority": get_domain_authority(domain),
        })
    return formatted


def render_source_cards(sources: list[dict]) -> str:
    """Render source cards as HTML (for HTMX partials)."""
    formatted = format_sources(sources)
    cards = []
    for s in formatted:
        cards.append(
            f'<div class="source-card" data-index="{s["index"]}">'
            f'<a href="{s["url"]}" target="_blank" class="source-link">'
            f'<span class="source-index">[{s["index"]}]</span> '
            f'<span class="source-title">{s["title"]}</span>'
            f'</a>'
            f'<div class="source-domain">{s["domain"]}</div>'
            f'<div class="source-snippet">{s["snippet"][:200]}</div>'
            f'</div>'
        )
    return "\n".join(cards)


def inject_citation_links(text: str) -> str:
    """Convert [1], [2] etc. in text to clickable HTML links."""
    def replace_cite(match):
        num = match.group(1)
        return f'<sup><a class="cite-link" href="#source-{num}">[{num}]</a></sup>'
    return re.sub(r"\[(\d+)\]", replace_cite, text)
