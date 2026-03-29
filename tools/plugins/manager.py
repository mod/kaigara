"""Plugin manager — discovers, loads, and manages tool plugins."""

import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class PluginManifest:
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    requires_env: list[str] = field(default_factory=list)
    provides_tools: list[str] = field(default_factory=list)
    provides_hooks: list[str] = field(default_factory=list)
    source: str = ""  # "directory", "entrypoint"
    path: str = ""


@dataclass
class LoadedPlugin:
    manifest: PluginManifest
    module: object | None = None
    tools_registered: list[str] = field(default_factory=list)
    hooks_registered: list[str] = field(default_factory=list)
    enabled: bool = True
    error: str | None = None


# Valid lifecycle hooks
VALID_HOOKS = {
    "pre_tool_call",
    "post_tool_call",
    "pre_llm_call",
    "post_llm_call",
    "on_session_start",
    "on_session_end",
}


class PluginContext:
    """Facade given to a plugin's register() function."""

    def __init__(self, registry, plugin_name: str):
        self._registry = registry
        self._plugin_name = plugin_name
        self.registered_tools: list[str] = []
        self.registered_hooks: list[str] = []

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler,
        *,
        toolset: str | None = None,
        check_fn=None,
        requires_env: list[str] | None = None,
        is_async: bool | None = None,
        emoji: str = "",
    ):
        """Register a tool into the global registry."""
        effective_toolset = toolset or f"plugin:{self._plugin_name}"
        self._registry.register(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            toolset=effective_toolset,
            check_fn=check_fn,
            requires_env=requires_env,
            is_async=is_async,
            emoji=emoji,
        )
        self.registered_tools.append(name)

    def register_hook(self, hook_name: str, callback):
        """Register a lifecycle hook callback."""
        if hook_name not in VALID_HOOKS:
            log.warning("Plugin '%s' tried to register invalid hook '%s'", self._plugin_name, hook_name)
            return
        self.registered_hooks.append(hook_name)


class PluginManager:
    """Discovers, loads, and manages plugins."""

    def __init__(self, registry):
        self._registry = registry
        self._plugins: dict[str, LoadedPlugin] = {}
        self._hooks: dict[str, list] = {h: [] for h in VALID_HOOKS}
        self._discovered = False

    def discover_and_load(self, plugins_dir: str | None = None):
        """Scan plugin sources and load all plugins. Idempotent."""
        if self._discovered:
            return
        self._discovered = True

        # Source 1: Directory plugins
        plugin_dir = Path(plugins_dir or os.environ.get(
            "KAIGARA_PLUGINS_DIR",
            str(Path.home() / ".kaigara" / "plugins")
        ))
        if plugin_dir.is_dir():
            self._scan_directory(plugin_dir)

        # Source 2: Project-local plugins
        project_plugins = Path.cwd() / ".kaigara" / "plugins"
        if project_plugins.is_dir() and os.environ.get("KAIGARA_ENABLE_PROJECT_PLUGINS"):
            self._scan_directory(project_plugins)

        # Source 3: pip-installed plugins (entry points)
        self._scan_entry_points()

        log.info("Loaded %d plugins", len(self._plugins))

    def _scan_directory(self, plugins_dir: Path):
        """Scan a directory for plugins."""
        for subdir in sorted(plugins_dir.iterdir()):
            if not subdir.is_dir() or subdir.name.startswith((".", "_")):
                continue

            manifest_path = subdir / "plugin.json"
            if not manifest_path.exists():
                # Try plugin.yaml
                manifest_path = subdir / "plugin.yaml"

            manifest = self._load_manifest(manifest_path, subdir.name)
            manifest.source = "directory"
            manifest.path = str(subdir)

            if manifest.name in self._plugins:
                continue

            self._load_plugin(manifest, subdir)

    def _scan_entry_points(self):
        """Load pip-installed plugins via entry points."""
        try:
            eps = importlib.metadata.entry_points()
            # Python 3.12+ returns SelectableGroups
            if hasattr(eps, "select"):
                plugin_eps = eps.select(group="kaigara.plugins")
            else:
                plugin_eps = eps.get("kaigara.plugins", [])

            for ep in plugin_eps:
                if ep.name in self._plugins:
                    continue
                manifest = PluginManifest(
                    name=ep.name,
                    source="entrypoint",
                )
                try:
                    module = ep.load()
                    self._register_plugin(manifest, module)
                except Exception as e:
                    log.error("Failed to load entry-point plugin '%s': %s", ep.name, e)
                    self._plugins[ep.name] = LoadedPlugin(manifest=manifest, error=str(e), enabled=False)
        except Exception:
            pass

    def _load_manifest(self, path: Path, fallback_name: str) -> PluginManifest:
        """Load plugin manifest from JSON or YAML file."""
        if path.exists():
            try:
                if path.suffix == ".yaml":
                    import yaml
                    data = yaml.safe_load(path.read_text())
                else:
                    data = json.loads(path.read_text())
                return PluginManifest(**{
                    k: v for k, v in data.items()
                    if k in PluginManifest.__dataclass_fields__
                })
            except Exception as e:
                log.warning("Failed to parse manifest %s: %s", path, e)

        return PluginManifest(name=fallback_name)

    def _load_plugin(self, manifest: PluginManifest, plugin_dir: Path):
        """Load a directory plugin."""
        init_file = plugin_dir / "__init__.py"
        if not init_file.exists():
            log.warning("Plugin '%s' has no __init__.py", manifest.name)
            return

        try:
            module_name = f"kaigara_plugins.{manifest.name}"
            spec = importlib.util.spec_from_file_location(module_name, str(init_file))
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load {init_file}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            self._register_plugin(manifest, module)
        except Exception as e:
            log.error("Failed to load plugin '%s': %s", manifest.name, e)
            self._plugins[manifest.name] = LoadedPlugin(manifest=manifest, error=str(e), enabled=False)

    def _register_plugin(self, manifest: PluginManifest, module):
        """Call plugin's register() function."""
        register_fn = getattr(module, "register", None)
        if not register_fn:
            log.warning("Plugin '%s' has no register() function", manifest.name)
            return

        ctx = PluginContext(self._registry, manifest.name)
        try:
            register_fn(ctx)
        except Exception as e:
            log.error("Plugin '%s' register() failed: %s", manifest.name, e)
            self._plugins[manifest.name] = LoadedPlugin(manifest=manifest, error=str(e), enabled=False)
            return

        # Store hook callbacks
        for hook_name in ctx.registered_hooks:
            callback = getattr(module, f"on_{hook_name}", None) or getattr(module, hook_name, None)
            if callback:
                self._hooks[hook_name].append(callback)

        loaded = LoadedPlugin(
            manifest=manifest,
            module=module,
            tools_registered=ctx.registered_tools,
            hooks_registered=ctx.registered_hooks,
        )
        self._plugins[manifest.name] = loaded
        log.info("Loaded plugin '%s' — %d tools, %d hooks",
                 manifest.name, len(ctx.registered_tools), len(ctx.registered_hooks))

    async def invoke_hook(self, hook_name: str, **kwargs):
        """Invoke all registered callbacks for a hook."""
        for callback in self._hooks.get(hook_name, []):
            try:
                if inspect.iscoroutinefunction(callback):
                    await callback(**kwargs)
                else:
                    callback(**kwargs)
            except Exception as e:
                log.error("Hook '%s' callback failed: %s", hook_name, e)

    def list_plugins(self) -> list[dict]:
        """Return plugin info for UI."""
        return [
            {
                "name": p.manifest.name,
                "version": p.manifest.version,
                "description": p.manifest.description,
                "source": p.manifest.source,
                "enabled": p.enabled,
                "tools": p.tools_registered,
                "hooks": p.hooks_registered,
                "error": p.error,
            }
            for p in self._plugins.values()
        ]


# Module-level singleton
_manager: PluginManager | None = None


def get_plugin_manager(registry) -> PluginManager:
    """Get or create the global plugin manager."""
    global _manager
    if _manager is None:
        _manager = PluginManager(registry)
    return _manager


def discover_plugins(registry):
    """Convenience: discover and load all plugins."""
    manager = get_plugin_manager(registry)
    manager.discover_and_load()
    return manager
