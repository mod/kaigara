"""MCP client — connects to MCP servers and registers their tools."""

import asyncio
import json
import logging
import os
import threading

log = logging.getLogger(__name__)

# Module-level state
_servers: dict[str, "MCPServerConnection"] = {}
_mcp_loop: asyncio.AbstractEventLoop | None = None
_mcp_thread: threading.Thread | None = None
_lock = threading.Lock()


class MCPServerConnection:
    """Manages a connection to a single MCP server."""

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.tools: list[dict] = []
        self._session = None
        self._client = None
        self._transport = None
        self._connected = False

    @property
    def is_http(self) -> bool:
        return bool(self.config.get("url"))

    async def connect(self) -> bool:
        """Connect to MCP server and discover tools."""
        try:
            from mcp import ClientSession  # noqa: F401
            if self.is_http:
                return await self._connect_http()
            else:
                return await self._connect_stdio()
        except ImportError:
            log.warning("mcp package not installed — skipping MCP server '%s'", self.name)
            return False
        except Exception as e:
            log.error("Failed to connect to MCP server '%s': %s", self.name, e)
            return False

    async def _connect_stdio(self) -> bool:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        command = self.config.get("command", "")
        args = self.config.get("args", [])
        env_vars = {**os.environ, **self.config.get("env", {})}

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=env_vars,
        )

        self._transport = stdio_client(server_params)
        read, write = await self._transport.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()

        # Discover tools
        result = await self._session.list_tools()
        self.tools = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            }
            for tool in result.tools
        ]
        self._connected = True
        log.info("Connected to MCP server '%s' — %d tools", self.name, len(self.tools))
        return True

    async def _connect_http(self) -> bool:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        url = self.config["url"]
        headers = self.config.get("headers", {})
        timeout = self.config.get("timeout", 120)

        self._transport = streamablehttp_client(url, headers=headers, timeout=timeout)
        read, write, _ = await self._transport.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()

        result = await self._session.list_tools()
        self.tools = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            }
            for tool in result.tools
        ]
        self._connected = True
        log.info("Connected to MCP server '%s' (HTTP) — %d tools", self.name, len(self.tools))
        return True

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call a tool on this MCP server."""
        if not self._session:
            return json.dumps({"error": f"MCP server '{self.name}' not connected"})
        try:
            result = await self._session.call_tool(tool_name, arguments)
            # Extract text content from result
            parts = []
            for content in result.content:
                if hasattr(content, "text"):
                    parts.append(content.text)
                elif hasattr(content, "data"):
                    parts.append(f"[binary data: {len(content.data)} bytes]")
            return "\n".join(parts) if parts else "(empty result)"
        except Exception as e:
            return json.dumps({"error": f"MCP tool call failed: {e}"})

    async def disconnect(self):
        """Disconnect from MCP server."""
        try:
            if self._session:
                await self._session.__aexit__(None, None, None)
            if self._transport:
                await self._transport.__aexit__(None, None, None)
        except Exception:
            pass
        self._connected = False


def _ensure_mcp_loop():
    """Start background event loop for MCP connections if not running."""
    global _mcp_loop, _mcp_thread
    with _lock:
        if _mcp_loop is not None:
            return

        def _run_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            global _mcp_loop
            _mcp_loop = loop
            loop.run_forever()

        _mcp_thread = threading.Thread(target=_run_loop, daemon=True)
        _mcp_thread.start()

        # Wait for loop to be ready
        import time
        for _ in range(50):
            if _mcp_loop is not None:
                break
            time.sleep(0.1)


def _run_on_mcp_loop(coro):
    """Schedule a coroutine on the MCP event loop and block until done."""
    _ensure_mcp_loop()
    future = asyncio.run_coroutine_threadsafe(coro, _mcp_loop)
    return future.result(timeout=120)


def _make_tool_handler(server_name: str, tool_name: str):
    """Create a sync handler that calls an MCP tool via the background loop."""
    async def _async_call(args: dict) -> str:
        server = _servers.get(server_name)
        if not server:
            return json.dumps({"error": f"MCP server '{server_name}' not available"})
        return await server.call_tool(tool_name, args)

    def handler(args: dict) -> str:
        return _run_on_mcp_loop(_async_call(args))

    return handler


async def discover_and_register(registry, config: dict):
    """Connect to all configured MCP servers and register their tools.

    Args:
        registry: ToolRegistry instance
        config: dict of {server_name: {command, args, env} or {url, headers}}
    """
    for name, server_config in config.items():
        server = MCPServerConnection(name, server_config)
        if await server.connect():
            _servers[name] = server
            # Register each tool
            for tool in server.tools:
                full_name = f"mcp_{name}_{tool['name']}"
                registry.register(
                    name=full_name,
                    description=f"[MCP:{name}] {tool['description']}",
                    parameters=tool.get("input_schema", {"type": "object", "properties": {}}),
                    handler=_make_tool_handler(name, tool["name"]),
                    toolset=f"mcp:{name}",
                    is_async=False,
                )
            log.info("Registered %d tools from MCP server '%s'", len(server.tools), name)


async def shutdown():
    """Disconnect all MCP servers."""
    for name, server in list(_servers.items()):
        await server.disconnect()
        log.info("Disconnected MCP server '%s'", name)
    _servers.clear()


def get_status() -> dict:
    """Return status of all MCP servers."""
    return {
        name: {
            "connected": server._connected,
            "tools": len(server.tools),
            "type": "http" if server.is_http else "stdio",
        }
        for name, server in _servers.items()
    }
