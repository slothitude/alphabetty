# Alphabetty

AI research assistant with web search, Chrome automation, deep research, knowledge graph, and agent-to-agent connectivity — accessible via web UI, MCP tools, SSE event stream, or REST API.

## What It Does

Alphabetty is a self-hosted research platform that combines web search, AI chat, headless Chrome browsing, and a local knowledge graph into a single tool. It's designed for deep research workflows where you need to search multiple sources, read pages, extract data, and synthesize findings — with everything saved and searchable.

**Core capabilities:**

- **AI Chat** — Conversational AI with web search integration. Every response is backed by sources with domain authority scoring. Supports multiple writing modes (concise, detailed, creative, academic, code).
- **Web Search** — SearXNG-powered search with adaptive query classification. Automatically categorizes queries (news, technical, academic) and optimizes search strategy.
- **Deep Research** — Multi-round automated research pipeline: plans search queries, executes them, extracts content from sources, identifies gaps in coverage, runs follow-up searches, and synthesizes a comprehensive report.
- **Agent Mode** — Autonomous AI agent with 19 tools (search, browse, click, type, scroll, tabs, macros, YouTube, PDF, delegation, sign-in). Runs multiple rounds with planning and reflection.
- **Chrome Automation** — Full headless Chrome control via CDP with anti-detection. Tab management, human-like clicking/typing/scrolling, wait-for-element, PDF printing, JavaScript evaluation, screenshots, DOM inspection, cookie management.
- **Macro Recording** — Record and replay browser interactions (API-level and browser-level) with timing-preserving playback. Screen recording via CDP screencast.
- **Sign-In Workflow** — Automated sign-in for any website with form detection, multi-step support (Google-style email→password), 2FA handling (manual code entry + automatic TOTP via pyotp), and saved credential profiles for one-click re-authentication.
- **Agent-to-Agent Connectivity** — SSE event bus for real-time notifications. Outbound agent bridge to delegate tasks to remote agents. External agents subscribe to events via `/api/events`.
- **Knowledge Graph** — FTS5 full-text search across all conversations, messages, and entities. Automatic entity extraction and relationship mapping. Tagging system for organizing research.
- **File Analysis** — Upload and analyze PDF, DOCX, text, and code files with AI-powered Q&A.
- **Image Generation** — Generate images via ComfyUI/FLUX pipeline.
- **Spaces** — Organize conversations into spaces for project-based research.
- **Export** — Export conversations as Markdown or PDF.

## Architecture

```
┌─────────────┐     ┌──────────────────────────────────────────────┐
│   Browser    │────▸│  FastAPI (port 7700)                         │
│   Web UI     │     │  ├── Chat / Search / Research / Agent        │
│   (HTMX+JS)  │     │  ├── Chrome CDP (headless, port 9222)       │
└─────────────┘     │  ├── Knowledge Graph (SQLite + FTS5)         │
                    │  ├── Macro Recording / Screen Recording       │
┌─────────────┐     │  ├── SSE Event Bus (page.load, research.done)│
│  MCP Server  │◂────┤  ├── Agent Bridge (outbound delegation)     │
│  (stdio)     │     │  └── Spaces / Export                         │
│  49 tools    │     │                                              │
└─────────────┘     │  REST Tools API                               │
                    │  GET  /api/v1/tools  (49 tool definitions)   │
┌─────────────┐     │  POST /api/v1/tools/call                     │
│  Claude Code │     │                                              │
│  GPT / Other │────▸│  SSE Event Stream                            │
└─────────────┘     │  GET  /api/events          (all events)      │
                    │  GET  /api/events/{type}   (filtered)        │
┌─────────────┐     └──────────────────────────────────────────────┘
│  Remote Agent│
│  (delegate)  │◂──── agent_bridge ──HTTP──▸ external MCP servers
└─────────────┘
```

1. **MCP Server** (`mcp_server.py`) — Stdio transport, HTTP proxy to running app. For Claude Code, Cursor, and any MCP-compatible client. 55 tools.
2. **REST Tools API** (`/api/v1/tools`) — OpenAI-format tool definitions + dispatch endpoint. For any agent with function calling.
3. **SSE Event Stream** (`/api/events`) — Real-time event notifications. External agents subscribe to page loads, research completion, macro finishes, etc.

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

Restart Claude Code — all 49 tools appear as `mcp__alphabetty__*`.

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

### SSE Event Stream (external agents)

```bash
# Subscribe to all events
curl -N http://localhost:7700/api/events

# Subscribe to specific event type
curl -N http://localhost:7700/api/events/page.loaded
curl -N http://localhost:7700/api/events/research.done
```

Event types: `page.loaded`, `research.done`, `macro.done`, `recording.done`, `youtube.playing`, `agent.done`, `tab.created`, `signin.started`, `signin.2fa_required`, `signin.done`, `signin.failed`

## Tool Catalog (55 Tools)

### Research & Search
| Tool | Description |
|------|-------------|
| `search` | SearXNG web search with adaptive classification |
| `deep_research` | Multi-round research: plan → search → extract → gap analysis → synthesize |
| `agent_research` | Autonomous agent with 16 browsing tools, planning, reflection |

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

### Macros & Recording
| Tool | Description |
|------|-------------|
| `macro_record_start` | Start API-level macro recording |
| `macro_record_stop` | Stop recording and save |
| `macro_record_browser` | Start browser-level recording (JS event listeners) |
| `macro_record_stop_browser` | Stop browser recording and save |
| `macro_play` | Replay a saved macro with timing |
| `macro_list` | List all saved macros |
| `screen_record_start` | Start screen recording via CDP screencast |
| `screen_record_stop` | Stop recording and compile video |

### YouTube
| Tool | Description |
|------|-------------|
| `youtube_play` | Search YouTube and play in Chrome |

### Sign-In
| Tool | Description |
|------|-------------|
| `signin_start` | Start sign-in flow (auto-detects form, handles multi-step like Google) |
| `signin_submit_2fa` | Submit 2FA verification code |
| `signin_check_2fa` | Check if page is asking for 2FA |
| `signin_status` | Current sign-in workflow state |
| `signin_auto` | Auto sign-in using saved credential (handles TOTP) |
| `signin_save` | Save a credential profile for auto sign-in |

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

## Agent Tools (19 Internal Tools)

The autonomous agent has access to these tools for multi-step research and automation:

| Tool | Description |
|------|-------------|
| `search` | Web search via SearXNG |
| `browse` | Navigate to URL and extract page text |
| `extract` | Extract text from CSS selector |
| `click` | Click an element |
| `type_text` | Type into an input field |
| `screenshot` | Capture current page state |
| `youtube_play` | Search and play YouTube video |
| `macro_record` | Start recording a macro |
| `macro_stop` | Stop recording and save |
| `macro_play` | Replay a saved macro |
| `tab_list` | List open Chrome tabs |
| `tab_new` | Open a new tab |
| `scroll` | Scroll page up/down |
| `wait_for` | Wait for element to appear |
| `print_pdf` | Print page as PDF |
| `delegate` | Delegate sub-task to another agent |
| `signin_start` | Start sign-in flow for a website (auto-detects login form) |
| `signin_2fa` | Submit 2FA/verification code |
| `signin_auto` | Auto sign-in using saved credential profile (handles TOTP) |

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
| `ALPHABETTY_AGENT_ENDPOINTS` | — | Comma-separated `name=url` for outbound agent delegation |

## Project Structure

```
alphabetty/
├── app.py              # FastAPI app factory, DB engine, router registration
├── config.py           # Pydantic settings (env vars with ALPHABETTY_ prefix)
├── mcp_server.py       # MCP server (stdio, HTTP proxy, 49 tools)
├── requirements.txt
├── Dockerfile          # Multi-service: FastAPI + Chrome + Xvfb
├── docker-compose.yml
├── api/                # FastAPI routers
│   ├── chat.py         #   Chat + conversation CRUD (SSE streaming)
│   ├── search.py       #   SearXNG search
│   ├── cdp.py          #   Chrome CDP control, tabs, macros, recording, YouTube, viewport
│   ├── research.py     #   Deep research pipeline
│   ├── agent.py        #   Autonomous agent (16 tools, planning, reflection)
│   ├── events.py       #   SSE event stream endpoints
│   ├── files.py        #   File upload & analysis
│   ├── images.py       #   Image generation
│   ├── spaces.py       #   Space management
│   ├── export.py       #   Markdown & PDF export
│   ├── graph.py        #   Knowledge graph + tags + FTS5
│   ├── signin.py       #   Sign-in workflow + credential CRUD
│   └── tools.py        #   REST tools API (OpenAI format, 55 tools)
├── core/               # Business logic
│   ├── llm.py          #   LLM streaming/calling, fallback, tool calling, AGENT_TOOLS
│   ├── searxng.py      #   Search with query classification
│   ├── cdp_bridge.py   #   Chrome DevTools Protocol client (navigate, click, tabs, wait, PDF)
│   ├── events.py       #   SSE pub/sub event bus (emit/subscribe)
│   ├── agent_bridge.py #   Outbound agent calling and delegation
│   ├── macro.py        #   Macro record/playback (API + browser level)
│   ├── recording.py    #   Screen recording via CDP screencast → WebM
│   ├── research_engine.py  # Multi-round research orchestration
│   ├── signin.py        #   Sign-in workflow (form detection, 2FA, TOTP)
│   ├── content_extractor.py # URL → clean text extraction
│   ├── source_citer.py #   Domain authority + source ranking
│   ├── graph.py        #   FTS5, entity extraction, knowledge graph
│   ├── file_analyzer.py #  File text extraction
│   └── chrome.py       #   Chrome launch + stealth injection
├── models/             # SQLAlchemy models
│   ├── conversation.py #   Conversation, Message, Source
│   ├── space.py        #   Space
│   ├── macro.py        #   Macro (recorded browser interactions)
│   ├── credential.py   #   Credential (saved sign-in profiles)
│   └── graph.py        #   Tag, Entity, EntityEdge, MessageEntity, DomainGraph
├── static/             # Frontend (vanilla JS + HTMX + Tailwind)
│   ├── css/app.css     #   Styles + toast notifications
│   └── js/app.js       #   App core + SSE event stream + toasts
│   └── js/signin.js    #   Sign-in UI panel
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
