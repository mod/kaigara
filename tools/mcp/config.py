"""MCP configuration — loads MCP server definitions."""

import json
import logging
import os
import re

log = logging.getLogger(__name__)


def _interpolate_env(value: str) -> str:
    """Replace ${VAR} placeholders with environment variable values."""
    def replace(match):
        var = match.group(1)
        return os.environ.get(var, match.group(0))
    return re.sub(r"\$\{(\w+)}", replace, value)


def _interpolate_dict(d: dict) -> dict:
    """Recursively interpolate env vars in dict values."""
    result = {}
    for k, v in d.items():
        if isinstance(v, str):
            result[k] = _interpolate_env(v)
        elif isinstance(v, dict):
            result[k] = _interpolate_dict(v)
        elif isinstance(v, list):
            result[k] = [_interpolate_env(i) if isinstance(i, str) else i for i in v]
        else:
            result[k] = v
    return result


def load_mcp_config() -> dict:
    """Load MCP server configuration.

    Sources (in priority order):
    1. MCP_SERVERS env var (JSON string)
    2. MCP_CONFIG_FILE env var pointing to a JSON file
    3. /data/mcp_servers.json (default config path)

    Returns: dict of {server_name: server_config}
    """
    # Try env var (JSON string)
    env_json = os.environ.get("MCP_SERVERS")
    if env_json:
        try:
            config = json.loads(env_json)
            return _interpolate_dict(config)
        except json.JSONDecodeError:
            log.error("Invalid JSON in MCP_SERVERS env var")
            return {}

    # Try config file
    config_path = os.environ.get("MCP_CONFIG_FILE", "/data/mcp_servers.json")
    try:
        with open(config_path) as f:
            config = json.load(f)
        return _interpolate_dict(config)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.error("Failed to load MCP config from %s: %s", config_path, e)
        return {}
