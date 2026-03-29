"""Tools container — FastAPI app with registry, MCP, and plugin initialization."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from tools.audit import log_request
from tools.builtins import browser_tools, cron_tools, file_tools, terminal_tools, web_tools
from tools.redactor import SecretRedactor
from tools.registry import ToolRegistry

log = logging.getLogger(__name__)

# Initialize tool registry with built-in tools
registry = ToolRegistry()
file_tools.register(registry)
web_tools.register(registry)
terminal_tools.register(registry)
browser_tools.register(registry)
cron_tools.register(registry)

# Register toolset metadata
registry.register_toolset("file", "File Operations", "Read, write, patch, and search files")
registry.register_toolset("web", "Web Tools", "Search and extract web content")
registry.register_toolset("terminal", "Terminal", "Execute shell commands")
registry.register_toolset("browser", "Browser", "Navigate and interact with web pages")
registry.register_toolset("cron", "Cron", "Schedule recurring agent tasks")

# Initialize redactor
redactor = SecretRedactor()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init MCP + plugins. Shutdown: cleanup MCP."""
    # Startup
    try:
        from tools.mcp.config import load_mcp_config
        from tools.mcp.client import discover_and_register
        mcp_config = load_mcp_config()
        if mcp_config:
            await discover_and_register(registry, mcp_config)
    except ImportError:
        log.debug("MCP support not available (mcp package not installed)")
    except Exception as e:
        log.warning("MCP initialization failed: %s", e)

    try:
        from tools.plugins.manager import discover_plugins
        discover_plugins(registry)
    except Exception as e:
        log.warning("Plugin initialization failed: %s", e)

    yield

    # Shutdown
    try:
        from tools.mcp.client import shutdown as mcp_shutdown
        await mcp_shutdown()
    except Exception:
        pass


app = FastAPI(title="kaigara-tools", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "tools"}


@app.get("/tools")
async def list_tools(toolset: str | None = None):
    """Return available tool schemas for the agent."""
    return registry.list_tools(toolset=toolset, check_available=True)


@app.get("/toolsets")
async def list_toolsets():
    """Return available toolsets and their status."""
    return registry.get_available_toolsets()


@app.post("/tool/{name}")
async def tool(name: str, payload: dict, request: Request):
    """Execute a registered tool by name."""
    role = request.headers.get("x-kaigara-role", "unknown")
    log_request(f"/tool/{name}", tool_name=name, role=role)

    result = await registry.dispatch(name, payload)

    # Redact any secrets from tool output
    redacted, was_redacted = redactor.redact(result)
    if was_redacted:
        log.warning("redacted secrets from tool '%s' output", name)

    return {"result": redacted}


@app.get("/mcp/status")
async def mcp_status():
    """Return MCP server connection status."""
    try:
        from tools.mcp.client import get_status
        return get_status()
    except ImportError:
        return {"error": "MCP not available"}


@app.get("/plugins")
async def list_plugins():
    """Return loaded plugins."""
    try:
        from tools.plugins.manager import get_plugin_manager
        manager = get_plugin_manager(registry)
        return manager.list_plugins()
    except Exception:
        return []
