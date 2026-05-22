"""Alphabetty Torrent Streamer — sequential download + HTTP streaming via libtorrent.

Runs as a sidecar behind VPN (shares transmission-vpn network namespace).
Provides HTTP range-request streaming as pieces become available.
"""

import asyncio
import logging
import os
import time
from pathlib import Path

import libtorrent as lt
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

app = FastAPI(title="Alphabetty Streamer")
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DOWNLOAD_DIR = os.environ.get("STREAMER_DOWNLOAD_DIR", "/downloads/streaming")
STREAMER_PORT = int(os.environ.get("STREAMER_PORT", "7701"))

# ─── Globals ───
_session: lt.session | None = None
_torrents: dict[str, dict] = {}


def _get_session() -> lt.session:
    global _session
    if _session is None:
        _session = lt.session()
        _session.apply_settings({
            "listen_interfaces": "0.0.0.0:6881",
            "user_agent": "AlphabettyStreamer/1.0",
        })
        _session.add_dht_router("router.bittorrent.com", 6881)
        _session.add_dht_router("router.utorrent.com", 6881)
        _session.start_dht()
        logger.info("libtorrent session started, DHT enabled")
    return _session


def _wait_metadata(handle, timeout=120):
    start = time.time()
    while not handle.has_metadata():
        if time.time() - start > timeout:
            raise TimeoutError("Metadata download timed out (no peers?)")
        time.sleep(0.5)


def _largest_file(info) -> tuple[int, int, str]:
    files = info.files()
    best_idx, best_size = 0, 0
    for i in range(files.num_files()):
        sz = files.file_size(i)
        if sz > best_size:
            best_size = sz
            best_idx = i
    return best_idx, best_size, files.file_path(best_idx)


def _read_range(file_path: str, offset: int, length: int) -> bytes:
    with open(file_path, "rb") as f:
        f.seek(offset)
        return f.read(length)


async def _add_magnet(magnet_uri: str) -> dict:
    p = lt.parse_magnet_uri(magnet_uri)
    p.save_path = DOWNLOAD_DIR
    p.flags |= lt.torrent_flags.sequential_download

    sess = _get_session()
    handle = sess.add_torrent(p)

    # Wait for metadata in thread pool (blocking)
    await asyncio.to_thread(_wait_metadata, handle)

    info = handle.get_torrent_info()
    fidx, fsize, fpath = _largest_file(info)

    # Only download the target file (skip the rest)
    prios = [0] * info.files().num_files()
    prios[fidx] = 7
    handle.prioritize_files(prios)

    file_name = os.path.basename(fpath)
    ih = str(handle.info_hash())

    # Ensure subdirectories exist
    os.makedirs(os.path.join(DOWNLOAD_DIR, os.path.dirname(fpath) if os.path.dirname(fpath) else ""), exist_ok=True)

    _torrents[ih] = {
        "handle": handle,
        "file_idx": fidx,
        "file_path": os.path.join(DOWNLOAD_DIR, fpath),
        "file_size": fsize,
        "file_name": file_name,
        "piece_length": info.piece_length(),
        "num_pieces": info.num_pieces(),
        "added_at": time.time(),
    }

    logger.info(f"Streaming: {file_name} ({fsize / 1024 / 1024:.1f}MB, {info.num_pieces()} pieces)")
    return {"info_hash": ih, "file_name": file_name, "file_size": fsize}


async def _stream_gen(handle, file_path, file_size, piece_length, start, end):
    """Async generator — yields file data as pieces become available."""
    start_piece = start // piece_length
    end_piece = min(end // piece_length, (file_size - 1) // piece_length)

    for pidx in range(start_piece, end_piece + 1):
        piece_start = pidx * piece_length
        read_start = max(piece_start, start)
        read_end = min(piece_start + piece_length, end + 1, file_size)
        if read_start >= read_end:
            continue

        # Wait for piece (up to 5 min)
        t0 = time.time()
        while not handle.have_piece(pidx):
            if time.time() - t0 > 300:
                logger.warning(f"Piece {pidx} timeout")
                return
            await asyncio.sleep(0.1)

        # Read from disk in thread pool
        try:
            data = await asyncio.to_thread(_read_range, file_path, read_start, read_end - read_start)
            if data:
                yield data
        except Exception as e:
            logger.error(f"Read error piece {pidx}: {e}")
            return


# ─── Endpoints ───

@app.post("/add")
async def add_stream(magnet: str = Query(..., description="Magnet URI")):
    """Add magnet URI for sequential streaming."""
    try:
        info = await _add_magnet(magnet)
    except TimeoutError as e:
        raise HTTPException(408, str(e))
    except Exception as e:
        logger.exception("Add failed")
        raise HTTPException(500, str(e))
    return {"status": "downloading", **info}


@app.get("/watch/{info_hash:path}")
async def watch(info_hash: str, request: Request):
    """Stream torrent file with HTTP range support for video players."""
    entry = _torrents.get(info_hash)
    if not entry:
        raise HTTPException(404, "Not found")

    handle = entry["handle"]
    fp = entry["file_path"]
    fs = entry["file_size"]
    pl = entry["piece_length"]
    fn = entry["file_name"]

    ext = Path(fn).suffix.lower()
    ctypes = {
        ".mp4": "video/mp4", ".mkv": "video/x-matroska", ".webm": "video/webm",
        ".avi": "video/x-msvideo", ".mov": "video/quicktime",
    }
    ct = ctypes.get(ext, "video/mp4")

    range_hdr = request.headers.get("range", "")

    if range_hdr:
        try:
            unit, spec = range_hdr.split("=")
            assert unit.strip() == "bytes"
            parts = spec.strip().split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else fs - 1
        except Exception:
            raise HTTPException(416, "Invalid range")
        end = min(end, fs - 1)
        return StreamingResponse(
            _stream_gen(handle, fp, fs, pl, start, end),
            status_code=206,
            headers={
                "Content-Range": f"bytes {start}-{end}/{fs}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(end - start + 1),
                "Content-Type": ct,
            },
        )

    # Full file request
    return StreamingResponse(
        _stream_gen(handle, fp, fs, pl, 0, fs - 1),
        headers={"Content-Length": str(fs), "Content-Type": ct, "Accept-Ranges": "bytes"},
    )


@app.get("/status/{info_hash}")
async def get_status(info_hash: str):
    """Get download progress for a streaming torrent."""
    entry = _torrents.get(info_hash)
    if not entry:
        raise HTTPException(404, "Not found")
    s = entry["handle"].status()
    return {
        "info_hash": info_hash,
        "file_name": entry["file_name"],
        "file_size": entry["file_size"],
        "progress": s.progress,
        "download_rate": s.download_rate,
        "num_peers": s.num_peers,
        "state": int(s.state),
    }


@app.get("/health")
async def health():
    return {"status": "ok", "torrents": len(_torrents)}


if __name__ == "__main__":
    import uvicorn
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=STREAMER_PORT)
