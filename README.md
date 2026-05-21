# Alphabetty

AI research assistant with web search, Chrome automation, deep research, and knowledge graph — all accessible via web UI, MCP tools, or REST API.

## What It Does

Alphabetty is a self-hosted research platform that combines web search, AI chat, headless Chrome browsing, and a local knowledge graph into a single tool. It's designed for deep research workflows where you need to search multiple sources, read pages, extract data, and synthesize findings — with everything saved and searchable.

**Core capabilities:**

- **AI Chat** — Conversational AI with web search integration. Every response is backed by sources with domain authority scoring. Supports multiple writing modes (concise, detailed, creative, academic, code).
- **Web Search** — SearXNG-powered search with adaptive query classification. Automatically categorizes queries (news, technical, academic) and optimizes search strategy.
- **Deep Research** — Multi-round automated research pipeline: plans search queries, executes them, extracts content from sources, identifies gaps in coverage, runs follow-up searches, and synthesizes a comprehensive report.
- **Agent Mode** — Autonomous AI agent that decides which tools to use (search, browse, click, extract, type), runs multiple rounds with planning and reflection, and produces a final answer.
- **Chrome Automation** — Full headless Chrome control via CDP with anti-detection. Human-like clicking, typing, and scrolling. JavaScript evaluation, screenshots, DOM inspection, cookie management.
- **Knowledge Graph** — FTS5 full-text search across all conversations, messages, and entities. Automatic entity extraction and relationship mapping. Tagging system for organizing research.
- **File Analysis** — Upload and analyze PDF, DOCX, text, and code files with AI-powered Q&A.
- **Image Generation** — Generate images via ComfyUI/FLUX pipeline.
- **Spaces** — Organize conversations into spaces for project-based research.
- **Export** — Export conversations as Markdown or PDF.

## Architecture

```
┌─────────────┐     ┌──────────────────────────────────────────┐
│   Browser    │────▸│  FastAPI (port 7700)                     │
│   Web UI     │     │  ├── Chat / Search / Research / Agent    │
│   (HTMX+JS)  │     │  ├── Chrome CDP (headless, port 9222)   │
└─────────────┘     │  ├── Knowledge Graph (SQLite + FTS5)     │
                    │  ├── File Analysis / Image Gen            │
┌─────────────┐     │  └── Spaces / Export                      │
│  MCP Server  │◂────┤                                          │
│  (stdio)     │     │  REST Tools API                          │
│  40 tools    │     │  GET  /api/v1/tools                      │
└─────────────┘     │  POST /api/v1/tools/call                  │
                    └──────────────────────────────────────────┘
┌─────────────┐
│  Claude Code │────▸  MCP (stdio) ──HTTP──▸ localhost:7700
│  GPT / Other │────▸  REST API ───────────▸ localhost:7700
└─────────────┘
```

**Two interfaces for external agents:**

1. **MCP Server** (`mcp_server.py`) — Stdio transport, HTTP proxy to running app. For Claude Code, Cursor, and any MCP-compatible client. 40 tools.
2. **REST Tools API** (`/api/v1/tools`) — OpenAI-format tool definitions + dispatch endpoint. For any agent with function calling.

## Quick Start

### Docker (recommended)

```bash
docker compose up --build
# Open http://localhost:7700
```

### Local Development

```bash
pip install -r requirements.txt

# Set environment variables (or create .env)
export ALPHABETTY_DB_PATH=./data/alphabetty.db
export ALPHABETTY_UPLOAD_DIR=./uploads
export ALPHABETTY_SEARXNG_URL=http://localhost:8888    # your SearXNG instance
export ALPHABETTY_LLM_API_KEY=your-key                 # Z.ai or OpenAI-compatible API

python -m uvicorn app:app --host 0.0.0.0 --port 7700 --reload
```

### MCP Integration (Claude Code)

Add to `~/.claude/.mcp.json`:

```json
{
  "alphabetty": {
    "command": "python",
    "args": ["./mcp_server.py"]
  }
}
```

Restart Claude Code — all 40 tools appear as `mcp__alphabetty__*`.

### REST API (any agent)

```bash
# List available tools (OpenAI function format)
curl http://localhost:7700/api/v1/tools

# Call a tool
curl -X POST http://localhost:7700/api/v1/tools/call \
  -H "Content-Type: application/json" \
  -d '{"tool_name": "search", "arguments": {"query": "quantum computing", "max_results": 5}}'

# Deep research
curl -X POST http://localhost:7700/api/v1/tools/call \
  -H "Content-Type: application/json" \
  -d '{"tool_name": "deep_research", "arguments": {"query": "impact of AI on scientific discovery", "depth": 3}}'

# Chat
curl -X POST http://localhost:7700/api/v1/tools/call \
  -H "Content-Type: application/json" \
  -d '{"tool_name": "chat", "arguments": {"query": "Explain transformer architectures", "mode": "detailed"}}'
```

## Tool Catalog (40 Tools)

### Research & Search
| Tool | Description |
|------|-------------|
| `search` | SearXNG web search with adaptive classification |
| `deep_research` | Multi-round research: plan → search → extract → gap analysis → synthesize |
| `agent_research` | Autonomous agent with browsing tools, planning, reflection |

### Chat
| Tool | Description |
|------|-------------|
| `chat` | AI chat with sources and follow-ups |
| `create_conversation` | Create a new conversation |
| `list_conversations` | List all conversations |
| `get_conversation` | Get conversation with full message history |
| `update_conversation` | Update title or mode |
| `delete_conversation` | Delete conversation |

### Chrome CDP
| Tool | Description |
|------|-------------|
| `chrome_status` | Chrome running status and stealth checks |
| `cdp_tabs` | List open tabs |
| `cdp_navigate` | Navigate to URL |
| `cdp_get_content` | Get page text |
| `cdp_get_dom` | Get DOM structure |
| `cdp_evaluate` | Run JavaScript |
| `cdp_click` | Human-like click with random offset |
| `cdp_type` | Human-like typing with random delays |
| `cdp_scroll` | Human-like scroll |
| `cdp_screenshot` | Screenshot (base64) |
| `cdp_extract` | Extract data via JS expression |
| `cdp_query` | CSS selector query |
| `cdp_get_cookies` | Get all cookies |
| `cdp_set_cookie` | Set a cookie |

### Knowledge Graph
| Tool | Description |
|------|-------------|
| `graph_search` | Full-text search across conversations/messages/entities |
| `graph_stats` | Knowledge graph statistics |
| `entity_graph` | Entity with neighbors and related conversations |
| `list_tags` | List all tags |
| `add_tag` | Tag a conversation |
| `remove_tag` | Remove a tag |

### Files & Images
| Tool | Description |
|------|-------------|
| `upload_file` | Upload file from local path (PDF, DOCX, code, images) |
| `analyze_file` | Analyze previously uploaded file |
| `generate_image` | Generate image via ComfyUI pipeline |

### Spaces & Export
| Tool | Description |
|------|-------------|
| `create_space` / `list_spaces` / `get_space` / `update_space` / `delete_space` | Space management |
| `add_to_space` | Add conversation to space |
| `export_markdown` | Export conversation as Markdown |
| `export_pdf` | Export conversation as PDF |

## Configuration

All settings use `ALPHABETTY_` prefix environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `ALPHABETTY_LLM_URL` | Z.ai API | OpenAI-compatible LLM endpoint |
| `ALPHABETTY_LLM_MODEL` | `glm-5.1` | Model name |
| `ALPHABETTY_LLM_API_KEY` | — | API key |
| `ALPHABETTY_OLLAMA_URL` | `http://localhost:11434` | Ollama fallback |
| `ALPHABETTY_SEARXNG_URL` | `http://localhost:8888` | SearXNG instance |
| `ALPHABETTY_CDP_URL` | `http://localhost:9222` | Chrome CDP endpoint |
| `ALPHABETTY_ROUTER_URL` | `http://localhost:4000` | Smart Router (images) |
| `ALPHABETTY_DB_PATH` | `/data/alphabetty.db` | SQLite database path |
| `ALPHABETTY_PORT` | `7700` | FastAPI port |
| `ALPHABETTY_UPLOAD_DIR` | `/uploads` | File upload directory |

## Project Structure

```
alphabetty/
├── app.py              # FastAPI app factory, DB engine, router registration
├── config.py           # Pydantic settings (env vars with ALPHABETTY_ prefix)
├── mcp_server.py       # MCP server (stdio, HTTP proxy, 40 tools)
├── requirements.txt
├── Dockerfile          # Multi-service: FastAPI + Chrome + Xvfb
├── docker-compose.yml
├── api/                # FastAPI routers
│   ├── chat.py         #   Chat + conversation CRUD (SSE streaming)
│   ├── search.py       #   SearXNG search
│   ├── cdp.py          #   Chrome CDP control (14 endpoints + WebSocket)
│   ├── research.py     #   Deep research pipeline
│   ├── agent.py        #   Autonomous agent mode
│   ├── files.py        #   File upload & analysis
│   ├── images.py       #   Image generation
│   ├── spaces.py       #   Space management
│   ├── export.py       #   Markdown & PDF export
│   ├── graph.py        #   Knowledge graph + tags + FTS5
│   └── tools.py        #   REST tools API (OpenAI format)
├── core/               # Business logic
│   ├── llm.py          #   LLM streaming/calling, fallback, tool calling
│   ├── searxng.py      #   Search with query classification
│   ├── cdp_bridge.py   #   Chrome DevTools Protocol client
│   ├── research_engine.py  # Multi-round research orchestration
│   ├── content_extractor.py # URL → clean text extraction
│   ├── source_citer.py #   Domain authority + source ranking
│   ├── graph.py        #   FTS5, entity extraction, knowledge graph
│   ├── file_analyzer.py #  File text extraction
│   └── chrome.py       #   Chrome launch + stealth injection
├── models/             # SQLAlchemy models
│   ├── conversation.py #   Conversation, Message, Source
│   ├── space.py        #   Space
│   └── graph.py        #   Tag, Entity, EntityEdge, MessageEntity, DomainGraph
├── static/             # Frontend (vanilla JS + HTMX + Tailwind)
├── templates/          # Jinja2 partials for HTMX
└── data/               # SQLite database storage
```

## Tech Stack

- **Backend:** FastAPI, SQLAlchemy (async), SQLite + FTS5, aiosqlite
- **Frontend:** Vanilla JS, HTMX, Tailwind CSS
- **AI:** Z.ai GLM-5.1 (primary), Ollama (fallback)
- **Search:** SearXNG (self-hosted)
- **Browser:** Headless Chrome via CDP with stealth injection
- **Images:** ComfyUI / FLUX pipeline
- **MCP:** FastMCP (stdio transport)
- **Deploy:** Docker (Chrome + Xvfb + FastAPI in one container)

## License

MIT
