# Alphabetty

AI research assistant with web search, Chrome automation, deep research, workflow automation, knowledge graph, and agent-to-agent connectivity — accessible via web UI, MCP tools, SSE event stream, or REST API.

## What It Does

Alphabetty is a self-hosted AI platform that combines web search, AI chat, headless Chrome browsing, workflow automation (n8n), and a local knowledge graph into a single tool. It runs as a swarm across your machines and the cloud, with multi-user auth and 57 MCP tools for external agents.

**Core capabilities:**

- **AI Chat** — Conversational AI with web search integration. Every response is backed by sources with domain authority scoring. Supports multiple writing modes (concise, detailed, creative, academic, code).
- **Web Search** — SearXNG-powered search with adaptive query classification. Automatically categorizes queries (news, technical, academic) and optimizes search strategy.
- **Deep Research** — Multi-round automated research pipeline: plans search queries, executes them, extracts content from sources, identifies gaps in coverage, runs follow-up searches, and synthesizes a comprehensive report.
- **Agent Mode** — Autonomous AI agent with 25 tools (search, browse, click, type, scroll, tabs, macros, YouTube, PDF, delegation, sign-in, workflows). Smart tool routing selects the right subset based on intent. Runs multiple rounds with planning and reflection.
- **Chrome Automation** — Full headless Chrome control via CDP with anti-detection. Tab management, human-like clicking/typing/scrolling, wait-for-element, PDF printing, JavaScript evaluation, screenshots, DOM inspection, cookie management, file uploads.
- **Macro Recording** — Record and replay browser interactions (API-level and browser-level) with variable interpolation, conditionals, error recovery, and timing-preserving playback. Screen recording via CDP screencast.
- **Sign-In Workflow** — Automated sign-in for any website with form detection, multi-step support (Google-style email→password), 2FA handling (manual code entry + automatic TOTP via pyotp), and saved credential profiles for one-click re-authentication.
- **Workflow Automation** — n8n sidecar for persistent automation: scheduled tasks, webhooks, API orchestration, data pipelines, and multi-step workflows with branching/loops. Create workflows via natural language through the agent or MCP tools.
- **Swarm Architecture** — Run multiple instances across machines (LAN + cloud) coordinated via MCP layer. Each instance operates independently with shared tool surface.
- **Multi-User Auth** — JWT cookies + API keys with user isolation. Ephemeral session users for CI/CD agents. Admin, demo, and custom user accounts.
- **Agent-to-Agent Connectivity** — SSE event bus for real-time notifications. Outbound agent bridge to delegate tasks to remote agents. External agents subscribe to events via `/api/events`.
- **Chrome Extension** — In-browser sidebar for AI chat with page context. Auto-connects when on an Alphabetty page (cookie-based handshake, no setup). WebSocket command relay lets the server control the user's actual browser tab — real cookies, real login state, no headless. Agents can execute JavaScript, click, type, navigate, and read page content through the extension.
- **Knowledge Graph** — FTS5 full-text search across all conversations, messages, and entities. Automatic entity extraction and relationship mapping. Tagging system for organizing research.
- **File Analysis** — Upload and analyze PDF, DOCX, text, and code files with AI-powered Q&A.
- **Image Generation** — Generate images via ComfyUI/FLUX pipeline.
- **Video Playback** — Play any video URL (YouTube, Twitter, TikTok, etc.) via yt-dlp stream extraction.
- **Spaces** — Organize conversations into spaces for project-based research.
- **Export** — Export conversations as Markdown or PDF.

## What You Can Build

Alphabetty is a platform, not just a tool. Here are some possibilities:

### Automated Monitoring
- "Watch this page every hour and email me if it changes" — n8n scheduled workflow + Chrome content extraction + email notification
- "Monitor these 5 news sites for mentions of [topic]" — recurring search + knowledge graph matching + digest generation
- "Track price changes on this product page" — scheduled scrape + comparison logic + alert

### Research Pipelines
- "Deep research [topic] and save a summary to my knowledge base every Monday" — n8n cron trigger → agent deep research → auto-tag and store
- "Whenever I tag a conversation as 'follow-up', create a research workflow" — SSE event listener → n8n webhook → automated research
- "Compare today's news against my existing research" — scheduled fetch + knowledge graph search + synthesis

### Browser Automation at Scale
- "Fill out this form every day at 9am with today's data" — n8n schedule → Chrome macro playback with variables
- "Record my login flow, then auto-login every session" — macro recording + credential saving + auto sign-in
- "Take screenshots of these 10 competitor sites weekly" — n8n workflow + multi-tab Chrome automation

### Agent-to-Agent Workflows
- Claude Code delegates research to Alphabetty → gets structured results back via MCP tools
- n8n webhook receives external trigger → spins up agent task → posts results to Slack/email
- Multiple Alphabetty instances coordinate: Oracle handles scheduled tasks, Lappy handles browser-heavy work

### Chrome Extension
- "Read the page I'm on and summarize it" — sidebar chat with page context injection
- Agent clicks through the user's actual browser: `ext_execute("click", {"selector": "#btn"})`
- "Navigate my browser to this URL and fill in the form" — ext_execute navigate + type
- Right-click selected text → "Ask Alphabetty about..." → pre-filled sidebar query

### Data Extraction & Processing
- "Extract all product names and prices from this category page" — navigate + extract + structured output
- "Download and analyze every PDF linked from this page" — crawl + file upload + analysis pipeline
- "Convert this webpage to clean Markdown" — content extraction with 30-min cache

### Multi-Instance Coordination
- **Oracle** (always-on cloud): scheduled workflows, webhooks, persistent automation
- **Lappy** (LAN heavyweight): browser-heavy tasks, GPU inference, video processing
- **Rog** (dev station): local testing, development, ad-hoc research

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
│  (stdio)     │     │  ├── n8n Workflows (automation sidecar)     │
│  57 tools    │     │  ├── Multi-User Auth (JWT + API keys)       │
└─────────────┘     │  ├── Extension WS (tab control relay)        │
                    │  └── Spaces / Export                         │
┌─────────────┐     │                                              │
│  Chrome Ext  │     │  Extension API                               │
│  (sidebar)   │◂───▸│  WS  /api/v1/ext/ws  (command relay)       │
│  WebSocket   │     │  POST /api/v1/ext/execute                   │
└─────────────┘     └──────────────────────────────────────────────┘
┌─────────────┐     ┌─────────────┐
│  Claude Code │     │  n8n Sidecar │
│  GPT / Other │────▸│  :5678       │◂────┤
└─────────────┘     └─────────────┘     │
                    ┌─────────────┐     │ SSE Event Stream
                    │  Remote Agent│     │ /api/events
                    │  (delegate)  │◂────┘
                    └─────────────┘
│  Remote Agent│
│  (delegate)  │◂──── agent_bridge ──HTTP──▸ external MCP servers
└─────────────┘
```

1. **MCP Server** (`mcp_server.py`) — Stdio transport, HTTP proxy to running app. For Claude Code, Cursor, and any MCP-compatible client. 57 tools.
2. **REST Tools API** (`/api/v1/tools`) — OpenAI-format tool definitions + dispatch endpoint. For any agent with function calling.
3. **SSE Event Stream** (`/api/events`) — Real-time event notifications. External agents subscribe to page loads, research completion, macro finishes, etc.
4. **n8n Sidecar** (`:5678`) — Persistent workflow automation. Scheduled tasks, webhooks, API orchestration. Controlled via agent/MCP tools or n8n UI.

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

Restart Claude Code — all 57 tools appear as `mcp__alphabetty__*`.

### Chrome Extension

1. Open Chrome → `chrome://extensions` → enable **Developer mode**
2. Click **Load unpacked** → select the `extension/` directory
3. Navigate to your Alphabetty instance and log in
4. Click the extension icon → sidebar opens and auto-connects (no setup needed)

Manual setup (for connecting to a different instance): click the gear icon in the sidebar, enter server URL and API key.

The extension adds a sidebar chat, page context injection, right-click "Ask Alphabetty about..." context menu, and a WebSocket command relay that lets the server control the active tab. Agents can use `ext_execute` to run JavaScript, click elements, type text, navigate, and read page content on the user's actual browser.

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

## Tool Catalog (57 Tools)

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

### Workflow Automation (n8n)
| Tool | Description |
|------|-------------|
| `workflow_list` | List all n8n workflows |
| `workflow_create` | Create workflow from node/connection JSON |
| `workflow_run` | Trigger workflow execution by ID |
| `workflow_status` | Check execution history for a workflow |
| `workflow_delete` | Delete a workflow |

### Chrome Extension (user's browser)
| Tool | Description |
|------|-------------|
| `ext_status` | Check if extension is connected |
| `ext_execute` | Run command on user's tab: evaluate, click, type, navigate, getDOM, getText |

## Agent Tools (25 Internal Tools)

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
| `video_play` | Play any video URL (yt-dlp extraction) |
| `macro_record` | Start recording a macro |
| `macro_stop` | Stop recording and save |
| `macro_play` | Replay a saved macro |
| `tab_list` | List open Chrome tabs |
| `tab_new` | Open a new tab |
| `scroll` | Scroll page up/down |
| `wait_for` | Wait for element to appear |
| `insert_text` | Fast native text insert (React/Vue compatible) |
| `click_at` | Fast click at exact pixel coordinates |
| `print_pdf` | Print page as PDF |
| `delegate` | Delegate sub-task to another agent |
| `signin_start` | Start sign-in flow for a website (auto-detects login form) |
| `signin_2fa` | Submit 2FA/verification code |
| `signin_auto` | Auto sign-in using saved credential profile (handles TOTP) |
| `workflow_list` | List all n8n workflows |
| `workflow_create` | Create n8n workflow from JSON |
| `workflow_run` | Trigger n8n workflow execution |
| `workflow_status` | Check workflow execution history |
| `workflow_delete` | Delete an n8n workflow |

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
| `ALPHABETTY_SECRET_KEY` | `change-me-in-production` | JWT secret |
| `ALPHABETTY_N8N_URL` | `http://n8n:5678` | n8n instance URL |
| `ALPHABETTY_N8N_API_KEY` | — | n8n public API key (JWT token) |
| `ALPHABETTY_API_KEY` | — | Static API key for external access |
| `ALPHABETTY_AGENT_ENDPOINTS` | — | Comma-separated `name=url` for outbound agent delegation |
| `ALPHABETTY_BOOTSTRAP_TOKEN` | `alphabetty-bootstrap-secret` | Token for session leasing |

## Project Structure

```
alphabetty/
├── app.py              # FastAPI app factory, DB engine, router registration
├── config.py           # Pydantic settings (env vars with ALPHABETTY_ prefix)
├── mcp_server.py       # MCP server (stdio, HTTP proxy, 55 tools)
├── requirements.txt
├── Dockerfile          # Multi-service: FastAPI + Chrome + Xvfb
├── docker-compose.yml  # alphabetty + n8n sidecar
├── deploy-oracle.sh    # Oracle Cloud Free Tier setup
├── api/                # FastAPI routers
│   ├── chat.py         #   Chat + conversation CRUD (SSE streaming)
│   ├── search.py       #   SearXNG search
│   ├── cdp.py          #   Chrome CDP control, tabs, macros, recording, YouTube, viewport
│   ├── research.py     #   Deep research pipeline
│   ├── agent.py        #   Autonomous agent (25 tools, smart routing, planning, reflection)
│   ├── auth.py         #   Auth endpoints (login, register, session leasing, JWT refresh)
│   ├── workflows.py    #   n8n workflow CRUD, run, executions, health
│   ├── events.py       #   SSE event stream endpoints
│   ├── files.py        #   File upload & analysis
│   ├── images.py       #   Image generation
│   ├── spaces.py       #   Space management
│   ├── export.py       #   Markdown & PDF export
│   ├── graph.py        #   Knowledge graph + tags + FTS5
│   ├── signin.py       #   Sign-in workflow + credential CRUD
│   └── tools.py        #   REST tools API (OpenAI format, 57 tools)
│   └── extension.py    #   Extension endpoints (ping, handshake, health, WS, execute)
├── core/               # Business logic
│   ├── llm.py          #   LLM streaming/calling, fallback, 25 agent tools, smart routing
│   ├── searxng.py      #   Search with query classification
│   ├── cdp_bridge.py   #   Chrome DevTools Protocol client (navigate, click, tabs, wait, PDF)
│   ├── events.py       #   SSE pub/sub event bus (emit/subscribe)
│   ├── auth.py         #   Auth logic — JWT, API keys, bcrypt, FastAPI dependencies
│   ├── n8n.py          #   n8n REST API client (CRUD, activate, execute, health)
│   ├── agent_bridge.py #   Outbound agent calling and delegation
│   ├── macro.py        #   Macro record/playback (API + browser level)
│   ├── recording.py    #   Screen recording via CDP screencast → WebM
│   ├── research_engine.py  # Multi-round research orchestration
│   ├── signin.py        #   Sign-in workflow (form detection, 2FA, TOTP)
│   ├── content_extractor.py # URL → clean text extraction (30-min cache)
│   ├── source_citer.py #   Domain authority + source ranking
│   ├── graph.py        #   FTS5, entity extraction, knowledge graph
│   ├── file_analyzer.py #  File text extraction
│   └── chrome.py       #   Chrome launch + stealth injection
├── models/             # SQLAlchemy models
│   ├── conversation.py #   Conversation, Message, Source
│   ├── user.py         #   User (auth, API keys, sessions)
│   ├── space.py        #   Space
│   ├── macro.py        #   Macro (recorded browser interactions)
│   ├── credential.py   #   Credential (saved sign-in profiles)
│   └── graph.py        #   Tag, Entity, EntityEdge, MessageEntity, DomainGraph
├── static/             # Frontend (vanilla JS + HTMX + Tailwind)
│   ├── css/app.css     #   Styles + toast notifications + dashboard
│   └── js/app.js       #   App core + SSE event stream + toasts
│   └── js/dashboard.js #   Dashboard (tabs, macros, system status)
│   └── js/signin.js    #   Sign-in UI panel
├── templates/          # Jinja2 partials for HTMX
├── extension/          # Chrome Extension (Manifest V3)
│   ├── manifest.json   #   Permissions: activeTab, storage, scripting, sidePanel, contextMenus, tabs, alarms
│   ├── background.js   #   Service worker: auto-connect, health, command relay
│   ├── sidepanel.html/js/css  # Sidebar chat UI with SSE streaming
│   ├── setup.html/js   # Manual setup (server URL + API key)
│   ├── content.js      # Content script: DOM commands (click, type, navigate, getDOM, getText)
│   └── lib/api.js      # Fetch wrapper + SSE consumer
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
