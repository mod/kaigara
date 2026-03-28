# Kaigara

A secure AI agent stack that uses container isolation to prevent secret extraction through conversation.

Forked from [hermes-agent](https://github.com/NousResearch/hermes-agent), stripped to a lean core.

## Problem

AI agents need API keys to call LLMs and tools. If those keys exist in the agent's process environment, a user can extract them through prompt injection, tool abuse, or shell commands like `env`. This is a blocker for making an agent publicly available.

## Solution

Split the agent into three containers. The one users talk to has zero secrets.

```
                    ┌─────────────────────┐
  Users ──────────► │  agent              │  NO secrets
   (public chat)    │  conversation loop  │
                    │  gateway            │
                    └────────┬────────────┘
                             │ internal network
              ┌──────────────┼──────────────┐
              │                             │
     ┌────────▼────────┐          ┌─────────▼─────────┐
     │  tools          │          │  sandbox           │
     │  LLM proxy      │          │  shell execution   │
     │  MCP servers    │          │  workspace volume  │
     │  tool APIs      │          │                    │
     │                 │          │  NO secrets        │
     │  ALL secrets    │          │  read-only root    │
     └─────────────────┘          └────────────────────┘
```

| Container | Secrets | Exposed to users | Purpose |
|-----------|---------|-----------------|---------|
| `agent` | none | yes (port 8080) | Conversation loop, gateway, session DB |
| `tools` | all API keys | no (internal only) | LLM proxy, tool/MCP execution |
| `sandbox` | none | no (internal only) | Shell command execution in workspace |

The agent sends structured HTTP requests to the tools and sandbox containers over the internal compose network. The tools container injects auth headers and proxies API calls. The sandbox runs shell commands in a read-only container with only a workspace volume mounted. Neither the agent nor the sandbox ever see API keys.

## Requirements

- [Podman](https://podman.io/) (or Docker)
- Podman machine running (`podman machine start`)

## Quickstart

```sh
# Clone
git clone <repo-url> kaigara && cd kaigara

# Configure secrets
cp .env.tools.example .env.tools
# Edit .env.tools with your API keys

# Start
podman-compose up -d --build

# Verify
curl http://localhost:8080/health
```

## Project Structure

```
kaigara/
├── Dockerfile            # Single image, all services
├── compose.yaml          # 3 containers with different commands
├── pyproject.toml        # Single dependency set (uv)
├── uv.lock
├── .env.tools            # All API secrets (gitignored)
├── agent/
│   ├── __init__.py
│   └── server.py         # FastAPI — public gateway, proxies to tools/sandbox
├── tools/
│   ├── __init__.py
│   └── server.py         # FastAPI — LLM proxy, tool execution (has secrets)
└── sandbox/
    ├── __init__.py
    └── server.py          # FastAPI — shell execution (no secrets)
```

One Dockerfile builds one image. `compose.yaml` runs it three times with different commands and env:

- `agent` gets `TOOLS_URL` and `SANDBOX_URL` only
- `tools` gets `.env.tools` with all API keys
- `sandbox` gets nothing — read-only filesystem, workspace volume, tmpfs

## API

### Agent (public, port 8080)

```
GET  /health              → {"status": "ok", "service": "agent"}
POST /chat                → proxies to tools /llm endpoint
POST /shell               → proxies to sandbox /exec endpoint
```

### Tools (internal, port 9000)

```
GET  /health              → {"status": "ok", "service": "tools"}
POST /llm                 → proxies to LLM provider with injected auth
POST /tool/{name}         → executes named tool
```

### Sandbox (internal, port 9001)

```
GET  /health              → {"status": "ok", "service": "sandbox"}
POST /exec                → runs shell command in /workspace, returns stdout/stderr/exit_code
```

## Security Model

- The agent container has **zero secrets** in its environment
- The sandbox container has **zero secrets** and a **read-only root filesystem**
- All API keys live exclusively in the tools container, which is **never exposed** to users
- Communication between containers uses **structured HTTP over internal network** only
- Shell commands cannot leak secrets because the sandbox simply doesn't have any
- Podman rootless mode provides additional host-level isolation

## Development

```sh
# Rebuild after code changes
podman-compose up -d --build

# View logs
podman-compose logs -f

# Stop
podman-compose down
```

## License

TBD
