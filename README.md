# Kaigara

A secure AI agent stack that uses container isolation to prevent tool secret extraction through conversation.

Forked from [hermes-agent](https://github.com/NousResearch/hermes-agent), stripped to a lean core.

## Problem

AI agents need tools with API keys (web search, code indexing, etc.). If those keys exist in the agent's process environment, a user can extract them through prompt injection, tool abuse, or shell commands like `env`. This is a blocker for making an agent publicly available.

## Solution

Split the stack into isolated containers. The agent calls LLM providers directly but never touches tool integration secrets.

```
                    ┌─────────────────────┐
  Users ──────────► │  agent              │  LLM keys only
   (public chat)    │  conversation loop  │
                    │  RBAC, sessions     │
                    └────────┬────────────┘
                             │ internal network
              ┌──────────────┼──────────────┐
              │                             │
     ┌────────▼────────┐          ┌─────────▼─────────┐
     │  tools           │          │  sandbox           │
     │  file, web,      │          │  shell execution   │
     │  terminal,       │          │  workspace volume  │
     │  browser, cron   │          │                    │
     │  MCP, plugins    │          │  NO secrets        │
     │                  │          │  read-only root    │
     │  tool secrets    │          │                    │
     └─────────────────┘          └────────────────────┘
              ▲
              │  optional
     ┌────────┴────────┐
     │  gateway         │
     │  Telegram, Slack │
     │  cron scheduler  │
     │  session mgmt    │
     └─────────────────┘
```

| Container | Secrets | Exposed to users | Purpose |
|-----------|---------|-----------------|---------|
| `agent` | LLM provider keys | yes (port 8080) | Conversation loop, direct LLM calls, RBAC, session DB |
| `tools` | tool integration keys | no (internal only) | Tool/MCP execution, plugins |
| `sandbox` | none | no (internal only) | Shell command execution in workspace |
| `gateway` | platform tokens | no (internal only) | Telegram/Slack bridge, cron scheduler |

## Requirements

- [Podman](https://podman.io/) (or Docker)
- Podman machine running (`podman machine start`)

## Quickstart

```sh
# Clone
git clone <repo-url> kaigara && cd kaigara

# Configure secrets
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY or OPENROUTER_API_KEY

# Start core stack (agent + tools + sandbox)
podman-compose up -d --build

# Verify
curl http://localhost:8080/health

# Interactive CLI
uv run kaigara
```

### Enable messaging gateway

```sh
# Add platform tokens to .env
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_ALLOWED_USERS=123456789

# Start with gateway profile
podman-compose --profile gateway up -d
```

### Install optional tool backends

```sh
# Browser automation
uv pip install kaigara[browser]
playwright install chromium

# MCP server support
uv pip install kaigara[mcp]

# Cron expressions
uv pip install kaigara[cron]

# Everything
uv pip install kaigara[all]
```

## Tools

Kaigara ships with 16 built-in tools organized into toolsets:

| Toolset | Tools | Backend |
|---------|-------|---------|
| **file** | `read_file`, `write_file`, `patch`, `search_files` | Sandbox container |
| **web** | `web_search`, `web_extract` | Firecrawl, Tavily, or SearXNG |
| **terminal** | `terminal` | Sandbox, Docker, or Podman |
| **browser** | `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_scroll`, `browser_back`, `browser_press`, `browser_close` | Playwright |
| **cron** | `cronjob` | Agent /chat API |

Additionally:
- **MCP tools** — connect any MCP server (stdio or HTTP) and its tools are auto-registered
- **Plugin tools** — drop a plugin into `~/.kaigara/plugins/` to add custom tools

### Terminal backends

The terminal tool supports multiple execution backends, configured via `TERMINAL_ENV`:

| Backend | `TERMINAL_ENV` | Description |
|---------|---------------|-------------|
| Sandbox proxy | `sandbox` (default) | Routes to the sandbox container |
| Local | `local` | Direct subprocess on host |
| Docker | `docker` | Executes in a Docker container with bind-mounted workspace |
| Podman | `podman` | Same as Docker but uses Podman runtime |

Docker/Podman containers are security-hardened: `--cap-drop ALL`, `--security-opt no-new-privileges`, `--pids-limit 256`.

### Web search backends

Auto-detected from available API keys, or forced via `WEB_BACKEND`:

| Backend | Env var | Notes |
|---------|---------|-------|
| Firecrawl | `FIRECRAWL_API_KEY` | Cloud or self-hosted (`FIRECRAWL_API_URL`) |
| Tavily | `TAVILY_API_KEY` | Cloud API |
| SearXNG | `SEARXNG_URL` | Self-hosted, no API key needed |

### MCP servers

Configure via `MCP_SERVERS` env var (JSON) or `MCP_CONFIG_FILE`:

```json
{
  "filesystem": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
  },
  "remote": {
    "url": "https://my-mcp-server.example.com/mcp",
    "headers": {"Authorization": "Bearer ${MCP_TOKEN}"}
  }
}
```

Supports `${VAR}` interpolation in config values.

### Plugins

Drop a directory into `~/.kaigara/plugins/`:

```
~/.kaigara/plugins/my_plugin/
├── plugin.json    # {"name": "my_plugin", "version": "1.0"}
└── __init__.py    # def register(ctx): ctx.register_tool(...)
```

Or install via pip (entry point group: `kaigara.plugins`).

Lifecycle hooks: `pre_tool_call`, `post_tool_call`, `pre_llm_call`, `post_llm_call`, `on_session_start`, `on_session_end`.

## Gateway

The gateway bridges messaging platforms to kaigara's agent API. It runs as a separate container and connects to the agent over the internal network.

### Telegram

Requires `TELEGRAM_BOT_TOKEN` (from [@BotFather](https://t.me/BotFather)). Optional:
- `TELEGRAM_ALLOWED_USERS` — comma-separated user IDs (empty = allow all)
- `TELEGRAM_HOME_CHANNEL` — chat ID for cron job delivery

### Slack

Requires Socket Mode. Set both:
- `SLACK_BOT_TOKEN` — `xoxb-...` bot token
- `SLACK_APP_TOKEN` — `xapp-...` app-level token

### Gateway commands

- `/new` — start a new conversation
- `/help` — show available commands

### Cron

Schedule recurring agent tasks:

```
Schedule formats:
  "30m"           — one-shot, 30 minutes from now
  "every 2h"      — recurring every 2 hours
  "0 9 * * *"     — cron expression (requires croniter)
```

Cron jobs execute by calling the agent's `/chat` API. Output is saved to `~/.kaigara/cron/output/` and optionally delivered to messaging platforms.

## Project Structure

```
kaigara/
├── compose.yaml              # 4 services (gateway optional)
├── pyproject.toml
├── agent/
│   ├── server.py             # FastAPI — public API, chat, sessions
│   ├── loop.py               # Conversation loop, tool dispatch
│   ├── llm.py                # LLM provider client (Anthropic, OpenRouter)
│   ├── clients.py            # HTTP clients for tools/sandbox
│   ├── rbac.py               # Role-based access control
│   ├── compression.py        # Context window management
│   ├── memory.py             # Persistent cross-session memory
│   ├── state.py              # SQLite session persistence + FTS
│   └── tokens.py             # Token counting
├── tools/
│   ├── server.py             # FastAPI — tool execution, MCP, plugins
│   ├── registry.py           # Tool registry with toolsets
│   ├── redactor.py           # Secret redaction on all output
│   ├── audit.py              # Request logging
│   ├── builtins/
│   │   ├── file_tools.py     # read, write, patch, search
│   │   ├── web_tools.py      # search + extract (multi-backend)
│   │   ├── terminal_tools.py # shell execution (multi-backend)
│   │   ├── browser_tools.py  # Playwright browser automation
│   │   └── cron_tools.py     # Cron job management
│   ├── environments/
│   │   ├── base.py           # BaseEnvironment ABC
│   │   ├── local.py          # Local + sandbox proxy
│   │   └── docker.py         # Docker/Podman
│   ├── mcp/
│   │   ├── client.py         # MCP server connections
│   │   └── config.py         # MCP config loading
│   └── plugins/
│       └── manager.py        # Plugin discovery + loading
├── sandbox/
│   ├── server.py             # FastAPI — shell execution (no secrets)
│   └── models.py             # Request/response models
├── gateway/
│   ├── run.py                # Gateway runner
│   ├── config.py             # Platform configuration
│   ├── session.py            # Session management
│   └── platforms/
│       ├── base.py           # BasePlatformAdapter
│       ├── telegram.py       # Telegram (python-telegram-bot)
│       └── slack.py          # Slack (slack-bolt, Socket Mode)
├── cron/
│   ├── jobs.py               # Job storage + scheduling
│   └── scheduler.py          # Execution loop
└── cli.py                    # Interactive REPL
```

## API

### Agent (public, port 8080)

```
GET  /health              → {"status": "ok", "service": "agent"}
GET  /                    → HTML chat interface
POST /chat                → conversation loop (direct LLM + tool dispatch)
POST /chat/stream         → SSE streaming chat
POST /shell               → proxies to sandbox /exec endpoint
GET  /sessions            → list recent sessions
GET  /sessions/search?q=  → full-text search across sessions
GET  /sessions/{id}       → get session with full message history
```

### Tools (internal, port 9000)

```
GET  /health              → {"status": "ok", "service": "tools"}
GET  /tools               → list available tool schemas (filterable by toolset)
GET  /toolsets            → list toolsets and their availability
POST /tool/{name}         → execute named tool
GET  /mcp/status          → MCP server connection status
GET  /plugins             → loaded plugins
```

### Sandbox (internal, port 9001)

```
GET  /health              → {"status": "ok", "service": "sandbox"}
POST /exec                → run shell command, returns stdout/stderr/exit_code
POST /exec/read           → read file from workspace
POST /exec/write          → write file to workspace
```

## RBAC

Three roles, enforced at the agent before dispatching to tools or sandbox:

| Role | Shell | Tools | Token budget |
|------|-------|-------|--------------|
| **Owner** | full access | all tools | 200k |
| **Member** | full access | elevated subset | 100k |
| **Guest** | no access | `web_search`, `web_extract`, `read_file`, `memory` only | 16k |

## Security Model

- **Agent** holds LLM provider keys only — isolated from tool integration secrets
- **Tools** container holds tool secrets only — never exposed to users, never sees LLM keys
- **Sandbox** has zero secrets and a read-only root filesystem
- **Gateway** holds platform tokens only — never sees LLM or tool secrets
- Communication between containers uses structured HTTP over an internal bridge network
- Shell commands cannot leak secrets because the sandbox simply doesn't have any
- All tool output passes through secret redaction before reaching the agent
- Podman rootless mode provides additional host-level isolation

## Development

```sh
# Rebuild after code changes
podman-compose up -d --build

# Run tests
uv run pytest tests/ -q

# View logs
podman-compose logs -f

# Stop
podman-compose down
```

## Environment Variables

### Agent
| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | one of these | Anthropic API key |
| `OPENROUTER_API_KEY` | one of these | OpenRouter API key |

### Tools
| Variable | Required | Description |
|----------|----------|-------------|
| `FIRECRAWL_API_KEY` | no | Firecrawl web search |
| `TAVILY_API_KEY` | no | Tavily web search |
| `SEARXNG_URL` | no | SearXNG instance URL |
| `TERMINAL_ENV` | no | Terminal backend: `sandbox`, `local`, `docker`, `podman` |
| `MCP_SERVERS` | no | MCP server config (JSON) |
| `GITHUB_TOKEN` | no | GitHub integration |

### Gateway
| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | no | Telegram bot token |
| `TELEGRAM_ALLOWED_USERS` | no | Comma-separated user IDs |
| `SLACK_BOT_TOKEN` | no | Slack bot token |
| `SLACK_APP_TOKEN` | no | Slack app-level token (Socket Mode) |

## License

TBD
