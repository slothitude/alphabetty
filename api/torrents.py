"""Torrent management — Jackett search + Transmission RPC proxy + file streaming."""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from config import settings
from core.auth import get_current_user
from models.user import User

router = APIRouter(tags=["torrents"])
logger = logging.getLogger(__name__)

# Transmission RPC uses a CSRF token (session header)
_session_token: str = ""


async def _transmission_rpc(method: str, arguments: dict = None) -> dict:
    """Call Transmission RPC. Handles CSRF token automatically."""
    global _session_token
    url = settings.transmission_url
    auth = (settings.transmission_user, settings.transmission_pass)

    payload = {"method": method}
    if arguments:
        payload["arguments"] = arguments

    headers = {}
    if _session_token:
        headers["X-Transmission-Session-Id"] = _session_token

    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(2):
            resp = await client.post(url, json=payload, headers=headers, auth=auth)

            if resp.status_code == 409:
                _session_token = resp.headers.get("X-Transmission-Session-Id", "")
                headers["X-Transmission-Session-Id"] = _session_token
                continue

            resp.raise_for_status()
            data = resp.json()
            if data.get("result") != "success":
                raise HTTPException(502, f"Transmission error: {data.get('result')}")
            return data

    raise HTTPException(502, "Transmission RPC failed after retry")


# ─── Models ───

class TorrentAddRequest(BaseModel):
    url: str  # magnet URI or .torrent URL

class TorrentSearchRequest(BaseModel):
    query: str
    auto_add: bool = False  # Automatically add best match

class TorrentRemoveRequest(BaseModel):
    delete_files: bool = False


# ─── Endpoints ───

@router.get("/torrents/list")
async def list_torrents(user: User = Depends(get_current_user)):
    """List all torrents with progress."""
    fields = ["id", "name", "status", "percentDone", "rateDownload", "rateUpload",
               "totalSize", "downloadDir", "hashString", "eta", "error", "files"]
    data = await _transmission_rpc("torrent-get", {"fields": fields})
    torrents = data.get("arguments", {}).get("torrents", [])
    return {"torrents": torrents, "count": len(torrents)}


@router.post("/torrents/add")
async def add_torrent(req: TorrentAddRequest, user: User = Depends(get_current_user)):
    """Add a magnet link or torrent URL to Transmission."""
    data = await _transmission_rpc("torrent-add", {"filename": req.url})
    added = data.get("arguments", {}).get("torrent-added") or data.get("arguments", {}).get("torrent-duplicate")
    if not added:
        raise HTTPException(500, "Failed to add torrent")
    return {"status": "added", "name": added.get("name", ""), "hash": added.get("hashString", "")}


@router.post("/torrents/search")
async def search_torrents(req: TorrentSearchRequest, user: User = Depends(get_current_user)):
    """Search Jackett (behind VPN) for torrents. Optionally auto-add best result."""
    jackett_url = settings.jackett_url
    jackett_key = settings.jackett_api_key

    # Query Jackett — all indexers, sorted by seeders
    search_url = (
        f"{jackett_url}/api/v2.0/indexers/all/results?"
        f"Query={quote(req.query)}&Categories=1000,2000,3000,4000,5000,6000,7000,8000"
        f"&apikey={jackett_key}"
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(search_url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Jackett search failed: {e}")
        raise HTTPException(502, f"Torrent search failed: {e}")

    results = []
    for r in data.get("Results", [])[:20]:
        magnet = r.get("MagnetUri", "") or r.get("Link", "")
        results.append({
            "title": r.get("Title", ""),
            "url": r.get("DetailsUrl", "") or r.get("Guid", ""),
            "magnet": magnet,
            "seeders": r.get("Seeders", 0),
            "leechers": r.get("Peers", 0),
            "size": _human_size(r.get("Size", 0)),
            "engine": r.get("Indexer", ""),
        })

    # Sort by seeders descending
    results.sort(key=lambda x: x.get("seeders", 0) or 0, reverse=True)

    auto_added = None
    if req.auto_add and results:
        best = results[0]
        link = best.get("magnet") or best.get("url", "")
        if link:
            try:
                data = await _transmission_rpc("torrent-add", {"filename": link})
                added = data.get("arguments", {}).get("torrent-added")
                if added:
                    auto_added = {"name": added.get("name", ""), "hash": added.get("hashString", "")}
            except Exception as e:
                logger.warning(f"Auto-add failed: {e}")

    return {"results": results, "count": len(results), "auto_added": auto_added}


@router.delete("/torrents/{torrent_id}")
async def remove_torrent(torrent_id: int, delete_files: bool = False, user: User = Depends(get_current_user)):
    """Remove a torrent. Optionally delete downloaded files."""
    await _transmission_rpc("torrent-remove", {
        "ids": [torrent_id],
        "delete-local-data": delete_files,
    })
    return {"status": "removed", "id": torrent_id, "files_deleted": delete_files}


@router.get("/torrents/stream/{torrent_id}")
async def stream_torrent_file(torrent_id: int, user: User = Depends(get_current_user)):
    """Stream the largest completed file from a torrent for browser playback."""
    fields = ["id", "name", "status", "percentDone", "downloadDir", "files",
               "fileStats", "totalSize"]
    data = await _transmission_rpc("torrent-get", {"ids": [torrent_id], "fields": fields})
    torrents = data.get("arguments", {}).get("torrents", [])
    if not torrents:
        raise HTTPException(404, "Torrent not found")

    torrent = torrents[0]
    if torrent.get("percentDone", 0) < 1.0:
        raise HTTPException(400, f"Torrent not complete ({torrent['percentDone']*100:.0f}%)")

    files = torrent.get("files", [])
    if not files:
        raise HTTPException(404, "No files in torrent")

    # Find largest file
    largest_idx = 0
    largest_size = 0
    for i, f in enumerate(files):
        size = f.get("length", 0)
        if size > largest_size:
            largest_size = size
            largest_idx = i

    file_info = files[largest_idx]
    file_name = file_info.get("name", "").split("/")[-1]

    download_dir = torrent.get("downloadDir", settings.torrent_dir)
    file_path = Path(download_dir) / file_info.get("name", file_name)

    if not file_path.exists():
        file_path = Path(download_dir) / file_name

    if not file_path.exists():
        raise HTTPException(404, f"File not found: {file_name}")

    ext = file_path.suffix.lower()
    content_types = {
        ".mp4": "video/mp4", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
        ".webm": "video/webm", ".mov": "video/quicktime", ".mp3": "audio/mpeg",
        ".flac": "audio/flac", ".wav": "audio/wav", ".ogg": "audio/ogg",
    }
    content_type = content_types.get(ext, "application/octet-stream")

    return FileResponse(
        str(file_path),
        media_type=content_type,
        filename=file_name,
    )


@router.get("/torrents/status")
async def transmission_status(user: User = Depends(get_current_user)):
    """Check if Transmission is reachable."""
    try:
        data = await _transmission_rpc("session-get", {})
        return {
            "status": "running",
            "version": data.get("arguments", {}).get("version", "unknown"),
            "download_dir": data.get("arguments", {}).get("download-dir", ""),
        }
    except Exception as e:
        return {"status": "offline", "error": str(e)}


def _human_size(size_bytes: int) -> str:
    """Convert bytes to human-readable size."""
    if not size_bytes:
        return ""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"

