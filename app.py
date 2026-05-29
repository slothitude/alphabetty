from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from config import settings
from models.conversation import Base
import models.user  # noqa: F401 — ensure table creation
import models.macro  # noqa: F401 — ensure table creation
import models.credential  # noqa: F401 — ensure table creation
import models.share  # noqa: F401 — ensure table creation

engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}", echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

templates = Jinja2Templates(directory="templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    # Init FTS5 + graph tables
    from core.graph import init_fts
    await init_fts()
    # Run auth migrations (add user_id columns, create default users)
    from core.migrate import run_migrations
    await run_migrations(engine, async_session)

    # Background tasks
    import asyncio
    import logging
    _bg_logger = logging.getLogger("alphabetty.bg")

    # Restore tabs from last session
    try:
        from core.cdp_bridge import cdp
        await asyncio.sleep(5)  # Wait for Chrome to fully start
        await cdp.restore_tabs()
    except Exception as e:
        _bg_logger.debug(f"Tab restore skipped: {e}")

    async def _chrome_health_check():
        """Periodically check Chrome health and emit events on crash."""
        while True:
            await asyncio.sleep(30)
            try:
                from core.cdp_bridge import cdp
                tabs = await cdp.get_tabs()
                _bg_logger.debug(f"Chrome health: {len(tabs)} tab(s)")
            except Exception as e:
                _bg_logger.warning(f"Chrome health check failed: {e}")
                from core.events import emit
                emit("chrome.offline", {"error": str(e)})

    async def _session_cleanup():
        """Periodically clean up stale session-* users."""
        while True:
            await asyncio.sleep(3600)  # Every hour
            try:
                from api.auth import cleanup_stale_sessions
                await cleanup_stale_sessions()
            except Exception as e:
                _bg_logger.warning(f"Session cleanup failed: {e}")

    async def _provider_refresh():
        """Periodically refresh model lists from all providers."""
        while True:
            await asyncio.sleep(1800)  # Every 30 min
            try:
                from core.providers import router as provider_router
                await provider_router.refresh_models()
            except Exception as e:
                _bg_logger.warning(f"Provider model refresh failed: {e}")

    # Startup: discover models from all providers (staggered)
    async def _discover_staggered(pr, name, delay):
        if delay:
            await asyncio.sleep(delay)
        await pr.refresh_models(name)

    try:
        from core.providers import router as provider_router
        provider_router._init_providers()
        for i, name in enumerate(provider_router.providers):
            asyncio.create_task(_discover_staggered(provider_router, name, i * 2))
    except Exception as e:
        _bg_logger.debug(f"Provider model discovery skipped: {e}")

    # Init swarm peers
    try:
        from core.swarm import swarm
        swarm.init_peers()
    except Exception as e:
        _bg_logger.debug(f"Swarm init skipped: {e}")

    # Acquire MCP session for SSE tools (delayed — server must be listening)
    async def _mcp_session_delayed():
        await asyncio.sleep(3)  # Wait for uvicorn to start accepting connections
        try:
            if _os.environ.get("ALPHABETTY_MCP_SSE", "").lower() in ("1", "true", "yes"):
                import mcp_server as _mcp_mod
                if _mcp_mod.BOOTSTRAP_TOKEN and not _mcp_mod.API_KEY:
                    await _mcp_mod._acquire_session()
                    if _mcp_mod._leased_key:
                        _bg_logger.info(f"MCP session acquired: key={_mcp_mod._leased_key[:8]}...")
        except Exception as e:
            _bg_logger.warning(f"MCP session acquire failed: {e}")
    asyncio.create_task(_mcp_session_delayed())

    async def _swarm_health_loop():
        """Periodically check swarm peer health."""
        while True:
            await asyncio.sleep(60)
            try:
                from core.swarm import swarm
                await swarm.health_check_all()
            except Exception as e:
                _bg_logger.debug(f"Swarm health check failed: {e}")

    async def _doctor_loop():
        """Periodically check Lappy service health and auto-heal."""
        while True:
            await asyncio.sleep(60)
            try:
                from core.doctor import run_health_checks
                await run_health_checks()
            except Exception as e:
                _bg_logger.debug(f"Doctor health check failed: {e}")

    chrome_task = asyncio.create_task(_chrome_health_check())
    cleanup_task = asyncio.create_task(_session_cleanup())
    provider_task = asyncio.create_task(_provider_refresh())
    swarm_task = asyncio.create_task(_swarm_health_loop())
    doctor_task = asyncio.create_task(_doctor_loop())
    yield
    chrome_task.cancel()
    cleanup_task.cancel()
    provider_task.cancel()
    swarm_task.cancel()
    doctor_task.cancel()


app = FastAPI(title="Alphabetty", lifespan=lifespan)

# CORS — allow Chrome extension origins + local dev
import re as _re
from starlette.middleware.cors import CORSMiddleware as _CORS

app.add_middleware(
    _CORS,
    allow_origin_regex=_re.compile(
        r"(chrome-extension://[a-z]{32}|http://localhost:\d+|http://192\.168\.0\.\d+:\d+|https?://152\.69\.184\.137(?::\d+)?)"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Register routers
from api.chat import router as chat_router
from api.search import router as search_router
from api.cdp import router as cdp_router
from api.research import router as research_router
from api.files import router as files_router
from api.images import router as images_router
from api.spaces import router as spaces_router
from api.export import router as export_router
from api.agent import router as agent_router
from api.graph import router as graph_router
from api.tools import router as tools_router
from api.events import router as events_router
from api.signin import router as signin_router
from api.auth import router as auth_router
from api.workflows import router as workflows_router
from api.extension import router as extension_router
from api.download import router as download_router
from api.share import router as share_router
from api.models import router as models_router
from api.swarm import router as swarm_router
from api.media import router as media_router


app.include_router(auth_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(search_router, prefix="/api/v1")
app.include_router(cdp_router, prefix="/api/v1")
app.include_router(research_router, prefix="/api/v1")
app.include_router(files_router, prefix="/api/v1")
app.include_router(images_router, prefix="/api/v1")
app.include_router(spaces_router, prefix="/api/v1")
app.include_router(export_router, prefix="/api/v1")
app.include_router(agent_router, prefix="/api/v1")
app.include_router(graph_router, prefix="/api/v1")
app.include_router(tools_router, prefix="/api/v1")
app.include_router(events_router, prefix="/api/v1")
app.include_router(signin_router, prefix="/api/v1")
app.include_router(workflows_router, prefix="/api/v1")
app.include_router(extension_router, prefix="/api/v1")
app.include_router(download_router, prefix="/api/v1")
app.include_router(share_router, prefix="/api/v1")
app.include_router(models_router, prefix="/api/v1")
app.include_router(swarm_router, prefix="/api/v1")
app.include_router(media_router, prefix="/api/v1")

# Backward compat: also mount all routers at /api (unversioned)
app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(search_router, prefix="/api")
app.include_router(cdp_router, prefix="/api")
app.include_router(research_router, prefix="/api")
app.include_router(files_router, prefix="/api")
app.include_router(images_router, prefix="/api")
app.include_router(spaces_router, prefix="/api")
app.include_router(export_router, prefix="/api")
app.include_router(agent_router, prefix="/api")
app.include_router(graph_router, prefix="/api")
app.include_router(tools_router, prefix="/api")
app.include_router(events_router, prefix="/api")
app.include_router(signin_router, prefix="/api")
app.include_router(workflows_router, prefix="/api")
app.include_router(extension_router, prefix="/api")
app.include_router(download_router, prefix="/api")
app.include_router(share_router, prefix="/api")
app.include_router(models_router, prefix="/api")
app.include_router(swarm_router, prefix="/api")
app.include_router(media_router, prefix="/api")


# ── MCP SSE endpoint for LLM agents (GhostKV, Claude Code, etc.) ────
import os as _os
_mcp_enabled = _os.environ.get("ALPHABETTY_MCP_SSE", "").lower() in ("1", "true", "yes")
if _mcp_enabled:
    from mcp_server import mcp as _mcp, _acquire_session, API_KEY, BOOTSTRAP_TOKEN
    _mcp_app = _mcp.http_app(transport="sse", path="/sse")
    from starlette.routing import Mount
    app.mount("/mcp", _mcp_app)
    print("MCP SSE endpoint mounted at /mcp/sse")


@app.get("/")
async def index(request: Request):
    from fastapi.responses import FileResponse
    from core.auth import get_optional_user, get_db
    from fastapi import Depends

    # Check if user is authenticated
    db_gen = get_db()
    db = await anext(db_gen)
    try:
        user = await get_optional_user(request, db)
        if user:
            return FileResponse("static/index.html")
        return FileResponse("static/login.html")
    finally:
        await db_gen.aclose()
