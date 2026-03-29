"""Tool registry — register, discover, and dispatch tools.

Enhanced with toolsets, availability checks, async bridging, and plugin support.
Adapted from hermes-agent's registry pattern.
"""

import asyncio
import inspect
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class ToolEntry:
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable  # sync or async handler(args: dict) -> str
    toolset: str = "default"
    check_fn: Callable[[], bool] | None = None  # returns True if tool is available
    requires_env: list[str] = field(default_factory=list)
    is_async: bool = False
    emoji: str = ""


class ToolRegistry:
    """Central tool registry — single source of truth for all tool schemas and handlers.

    Zero dependencies on other kaigara modules (safe against circular imports).
    """

    def __init__(self):
        self._tools: dict[str, ToolEntry] = {}
        self._toolset_meta: dict[str, dict] = {}  # toolset -> {label, description}
        self._lock = threading.Lock()

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable,
        *,
        toolset: str = "default",
        check_fn: Callable[[], bool] | None = None,
        requires_env: list[str] | None = None,
        is_async: bool | None = None,
        emoji: str = "",
    ):
        """Register a tool. Can be called at module-import time."""
        if is_async is None:
            is_async = inspect.iscoroutinefunction(handler)

        entry = ToolEntry(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            toolset=toolset,
            check_fn=check_fn,
            requires_env=requires_env or [],
            is_async=is_async,
            emoji=emoji,
        )

        with self._lock:
            self._tools[name] = entry

    def register_toolset(self, key: str, label: str, description: str = ""):
        """Register toolset metadata for UI/discovery."""
        self._toolset_meta[key] = {"label": label, "description": description}

    async def dispatch(self, name: str, args: dict, **kwargs) -> str:
        """Execute a tool handler by name. Auto-bridges sync/async handlers."""
        entry = self._tools.get(name)
        if not entry:
            return f"error: unknown tool '{name}'"

        if entry.check_fn and not entry.check_fn():
            return f"error: tool '{name}' is not available (missing requirements)"

        try:
            if entry.is_async:
                return await entry.handler(args, **kwargs)
            else:
                # Bridge sync handler — run in thread pool to avoid blocking
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, lambda: entry.handler(args, **kwargs))
        except TypeError:
            # Handler doesn't accept **kwargs — call with just args
            try:
                if entry.is_async:
                    return await entry.handler(args)
                else:
                    loop = asyncio.get_running_loop()
                    return await loop.run_in_executor(None, lambda: entry.handler(args))
            except Exception as e:
                log.exception("tool '%s' failed", name)
                return f"error: {e}"
        except Exception as e:
            log.exception("tool '%s' failed", name)
            return f"error: {e}"

    def list_tools(self, *, toolset: str | None = None, check_available: bool = False) -> list[dict]:
        """Return OpenAI-compatible function schemas.

        Args:
            toolset: Filter to a specific toolset.
            check_available: If True, only return tools whose check_fn passes.
        """
        results = []
        for entry in self._tools.values():
            if toolset and entry.toolset != toolset:
                continue
            if check_available and entry.check_fn and not entry.check_fn():
                continue
            results.append({
                "type": "function",
                "function": {
                    "name": entry.name,
                    "description": entry.description,
                    "parameters": entry.parameters,
                },
            })
        return results

    def get_tool(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

    def get_all_tool_names(self) -> list[str]:
        return sorted(self._tools.keys())

    def get_toolset_for_tool(self, name: str) -> str | None:
        entry = self._tools.get(name)
        return entry.toolset if entry else None

    def get_toolsets(self) -> dict[str, list[str]]:
        """Return {toolset_name: [tool_names]}."""
        result: dict[str, list[str]] = {}
        for entry in self._tools.values():
            result.setdefault(entry.toolset, []).append(entry.name)
        return result

    def get_available_toolsets(self) -> list[dict]:
        """Return toolset info for UI display."""
        toolsets = self.get_toolsets()
        results = []
        for key, tools in sorted(toolsets.items()):
            meta = self._toolset_meta.get(key, {})
            available = all(
                (not self._tools[t].check_fn or self._tools[t].check_fn())
                for t in tools
            )
            results.append({
                "key": key,
                "label": meta.get("label", key),
                "description": meta.get("description", ""),
                "tools": tools,
                "available": available,
            })
        return results

    def check_requirements(self) -> dict[str, bool]:
        """Check which toolsets have their requirements met."""
        toolsets = self.get_toolsets()
        return {
            key: all(
                (not self._tools[t].check_fn or self._tools[t].check_fn())
                for t in tools
            )
            for key, tools in toolsets.items()
        }
