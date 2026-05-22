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
    yield


app = FastAPI(title="Alphabetty", lifespan=lifespan)

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


# ── MCP SSE endpoint for LLM agents (GhostKV, Claude Code, etc.) ────
import os as _os
_mcp_enabled = _os.environ.get("ALPHABETTY_MCP_SSE", "").lower() in ("1", "true", "yes")
if _mcp_enabled:
    from mcp_server import mcp as _mcp
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
