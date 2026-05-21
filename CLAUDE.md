# Alphabetty — Chrome Control + AI Research Assistant

## Quick Start
```bash
docker compose up --build
# Open http://localhost:7700
```

## Architecture
- FastAPI backend (Python 3.13) on port 7700
- Headless Chrome (CDP) on port 9222 inside container
- SearXNG (external) for search
- Z.ai GLM-5.1 / Ollama fallback for LLM

## Key Paths
- `app.py` — FastAPI app factory, DB init
- `config.py` — pydantic-settings (env vars with ALPHABETTY_ prefix)
- `api/` — FastAPI routers (chat, search, cdp, research, files, images, spaces, export)
- `core/` — Business logic (llm, searxng, cdp_bridge, research_engine, etc.)
- `models/` — SQLAlchemy models
- `static/` — Frontend (vanilla JS + HTMX + Tailwind)
- `templates/partials/` — Jinja2 partials for HTMX
