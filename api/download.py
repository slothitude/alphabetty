"""Download proxy — stream any URL as a file attachment."""

import logging
from urllib.parse import urlparse, unquote
from pathlib import PurePosixPath

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.auth import get_current_user
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["download"])

MAX_SIZE = 500 * 1024 * 1024  # 500 MB
CHUNK_SIZE = 64 * 1024  # 64 KB


def _filename_from_response(url: str, resp: httpx.Response) -> str:
    """Extract filename from Content-Disposition header or URL path."""
    cd = resp.headers.get("content-disposition", "")
    if "filename=" in cd:
        for part in cd.split(";"):
            part = part.strip()
            if part.startswith("filename="):
                name = part.split("=", 1)[1].strip().strip('"').strip("'")
                if name:
                    return name

    # Fallback: use last path segment
    parsed = urlparse(url)
    name = unquote(PurePosixPath(parsed.path).name)
    if name:
        return name

    return "download"


async def _stream_url(url: str, filename: str | None = None) -> StreamingResponse:
    """Fetch a URL via httpx and stream it back as a file download."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=True,
    ) as client:
        # Head request to check size
        head = await client.head(url)
        head.raise_for_status()

        content_length = head.headers.get("content-length")
        if content_length and int(content_length) > MAX_SIZE:
            return StreamingResponse(
                iter([f"File too large ({int(content_length) // (1024*1024)} MB). Limit is {MAX_SIZE // (1024*1024)} MB."]),
                media_type="text/plain",
                status_code=413,
            )

        if not filename:
            filename = _filename_from_response(url, head)

        content_type = head.headers.get("content-type", "application/octet-stream")

        async def _aiter():
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(CHUNK_SIZE):
                    yield chunk

        return StreamingResponse(
            _aiter(),
            media_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


@router.get("/download/proxy")
async def download_proxy(
    url: str = Query(..., description="URL to download"),
    filename: str | None = Query(None, description="Override filename"),
    user: User = Depends(get_current_user),
):
    """Proxy any URL as a file download. Streams content back with Content-Disposition: attachment."""
    try:
        return await _stream_url(url, filename)
    except httpx.HTTPStatusError as e:
        return {"error": f"Upstream returned {e.response.status_code}", "url": url}
    except httpx.RequestError as e:
        return {"error": f"Failed to fetch: {e}", "url": url}


class CDPDownloadRequest(BaseModel):
    tab_id: str | None = None
    selector: str | None = None
    url: str | None = None
    filename: str | None = None


@router.post("/download/cdp")
async def download_cdp(
    req: CDPDownloadRequest,
    user: User = Depends(get_current_user),
):
    """Download a file from the headless Chrome context.

    - If `url` provided: fetches directly via httpx (same as proxy)
    - If `selector` provided: extracts href/src from that element via CDP, then fetches
    """
    target_url = req.url

    # If no URL but selector given, extract href/src from Chrome
    if not target_url and req.selector:
        try:
            from core.cdp_bridge import cdp
            js = f"""
                (() => {{
                    const el = document.querySelector({req.selector!r});
                    if (!el) return JSON.stringify({{error: 'element not found'}});
                    return JSON.stringify({{url: el.href || el.src || el.getAttribute('data-url') || ''}});
                }})()
            """
            result = await cdp.evaluate(js, tab_id=req.tab_id or "")
            if isinstance(result, dict):
                target_url = result.get("url", "")
            elif isinstance(result, str):
                import json
                parsed = json.loads(result)
                target_url = parsed.get("url", "")
        except Exception as e:
            return {"error": f"CDP selector extraction failed: {e}"}

    if not target_url:
        return {"error": "No URL provided and selector did not yield one"}

    try:
        return await _stream_url(target_url, req.filename)
    except httpx.HTTPStatusError as e:
        return {"error": f"Upstream returned {e.response.status_code}", "url": target_url}
    except httpx.RequestError as e:
        return {"error": f"Failed to fetch: {e}", "url": target_url}
