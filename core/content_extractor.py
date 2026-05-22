import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MAX_CONTENT_LENGTH = 50000
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Simple TTL cache: url -> (timestamp, result)
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 1800  # 30 minutes


async def fetch_and_extract(url: str, max_length: int = MAX_CONTENT_LENGTH) -> dict:
    """Fetch a URL and extract clean text content. Results cached for 30 min."""
    # Check cache
    if url in _cache:
        ts, cached = _cache[url]
        if time.time() - ts < _CACHE_TTL:
            logger.debug(f"Cache hit: {url[:80]}")
            return cached

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return {"url": url, "title": "", "text": "", "error": str(e)}

    result = extract_content(html, url)
    _cache[url] = (time.time(), result)
    return result


def extract_content(html: str, url: str = "") -> dict:
    """Extract title and clean text from HTML."""
    soup = BeautifulSoup(html, "lxml")

    # Remove unwanted elements
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Try to find main content area
    main = soup.find("main") or soup.find("article") or soup.find("div", class_=re.compile(r"content|article|post|entry", re.I))
    content_el = main if main else soup.body

    if not content_el:
        return {"url": url, "title": title, "text": ""}

    text = content_el.get_text(separator="\n", strip=True)

    # Clean up excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text[:MAX_CONTENT_LENGTH]

    return {"url": url, "title": title, "text": text}


def chunk_text(text: str, chunk_size: int = 4000, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks for processing."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if end < len(text):
            # Try to break at sentence boundary
            last_period = chunk.rfind(".")
            if last_period > chunk_size * 0.7:
                end = start + last_period + 1
                chunk = text[start:end]
        chunks.append(chunk)
        start = end - overlap
    return chunks
