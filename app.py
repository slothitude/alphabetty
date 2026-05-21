from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from config import settings
from models.conversation import Base

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


@app.get("/")
async def index():
    from fastapi.responses import FileResponse
    return FileResponse("static/index.html")
