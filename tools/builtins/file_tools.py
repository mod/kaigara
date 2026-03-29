"""File tools — read, write, patch, and search files in the sandbox workspace."""

import json
import os
import logging

import httpx

log = logging.getLogger(__name__)

SANDBOX_URL = os.environ.get("SANDBOX_URL", "http://sandbox:9001")


async def _sandbox_exec(command: str, timeout: int = 15) -> dict:
    """Execute a command in the sandbox and return the result."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SANDBOX_URL}/exec",
            json={"command": command, "timeout": timeout},
            timeout=timeout + 10,
        )
        return resp.json()


async def _sandbox_read(path: str) -> str | None:
    """Read a file from sandbox. Returns content or None on error."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{SANDBOX_URL}/exec/read", json={"path": path}, timeout=10)
        if resp.status_code != 200:
            return None
        return resp.json().get("content", "")


async def _sandbox_write(path: str, content: str) -> str | None:
    """Write a file to sandbox. Returns None on success, error string on failure."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SANDBOX_URL}/exec/write",
            json={"path": path, "content": content},
            timeout=10,
        )
        if resp.status_code != 200:
            return resp.json().get("detail", "write failed")
        return None


async def read_file(args: dict) -> str:
    path = args.get("path", "")
    offset = max(args.get("offset", 1), 1)
    limit = min(args.get("limit", 500), 2000)

    content = await _sandbox_read(path)
    if content is None:
        return json.dumps({"error": f"file not found: {path}"})

    lines = content.split("\n")
    total_lines = len(lines)

    # Apply pagination
    start = offset - 1
    end = start + limit
    selected = lines[start:end]

    # Format with line numbers
    numbered = "\n".join(
        f"{i + offset}\t{line}" for i, line in enumerate(selected)
    )

    result = {"content": numbered, "total_lines": total_lines}
    if end < total_lines:
        result["has_more"] = True
        result["next_offset"] = end + 1
    return json.dumps(result)


async def write_file(args: dict) -> str:
    path = args.get("path", "")
    content = args.get("content", "")
    err = await _sandbox_write(path, content)
    if err:
        return json.dumps({"error": err})
    return json.dumps({"status": "written", "path": path})


async def patch(args: dict) -> str:
    path = args.get("path", "")
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    replace_all = args.get("replace_all", False)

    if not path or not old_string:
        return json.dumps({"error": "path and old_string are required"})

    content = await _sandbox_read(path)
    if content is None:
        return json.dumps({"error": f"file not found: {path}"})

    if old_string not in content:
        return json.dumps({"error": "old_string not found in file"})

    if not replace_all:
        count = content.count(old_string)
        if count > 1:
            return json.dumps({"error": f"old_string found {count} times — use replace_all=true or provide more context"})
        new_content = content.replace(old_string, new_string, 1)
    else:
        new_content = content.replace(old_string, new_string)

    err = await _sandbox_write(path, new_content)
    if err:
        return json.dumps({"error": err})

    replacements = content.count(old_string) if replace_all else 1
    return json.dumps({"status": "patched", "path": path, "replacements": replacements})


async def search_files(args: dict) -> str:
    pattern = args.get("pattern", "")
    target = args.get("target", "content")
    path = args.get("path", "/workspace")
    file_glob = args.get("file_glob", "")
    limit = min(args.get("limit", 50), 200)
    context_lines = args.get("context", 0)
    output_mode = args.get("output_mode", "content")

    if not pattern:
        return json.dumps({"error": "pattern is required"})

    if target == "files":
        # Find files by glob pattern
        cmd = f"find {path} -name '{pattern}' -type f 2>/dev/null | head -{limit}"
    else:
        # Content search with grep/ripgrep
        rg = "rg" if os.environ.get("HAS_RIPGREP") else "grep -rn"
        glob_flag = ""
        if file_glob:
            glob_flag = f"--include='{file_glob}'" if "grep" in rg else f"-g '{file_glob}'"

        context_flag = f"-C {context_lines}" if context_lines else ""

        if output_mode == "files_only":
            mode_flag = "-l" if "grep" in rg else "--files-with-matches"
        elif output_mode == "count":
            mode_flag = "-c" if "grep" in rg else "--count"
        else:
            mode_flag = ""

        cmd = f"{rg} {context_flag} {mode_flag} {glob_flag} '{pattern}' {path} 2>/dev/null | head -{limit}"

    data = await _sandbox_exec(cmd, timeout=30)
    output = data.get("stdout", "") or data.get("stderr", "no matches")
    return output


def register(registry):
    """Register file tools."""
    registry.register(
        name="read_file",
        description="Read a file with line numbers and pagination.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (relative to /workspace)"},
                "offset": {"type": "integer", "description": "Line to start reading (1-indexed, default 1)", "minimum": 1, "default": 1},
                "limit": {"type": "integer", "description": "Max lines to return (default 500, max 2000)", "default": 500, "maximum": 2000},
            },
            "required": ["path"],
        },
        handler=read_file,
        toolset="file",
        emoji="📖",
    )
    registry.register(
        name="write_file",
        description="Write content to a file, completely replacing existing content.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (created if missing, overwritten if exists)"},
                "content": {"type": "string", "description": "Complete content to write"},
            },
            "required": ["path", "content"],
        },
        handler=write_file,
        toolset="file",
        emoji="✍️",
    )
    registry.register(
        name="patch",
        description="Find and replace text in a file. Fails if old_string is not unique (unless replace_all=true).",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File to edit"},
                "old_string": {"type": "string", "description": "Text to find"},
                "new_string": {"type": "string", "description": "Replacement text"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences", "default": False},
            },
            "required": ["path", "old_string", "new_string"],
        },
        handler=patch,
        toolset="file",
        emoji="🔧",
    )
    registry.register(
        name="search_files",
        description="Search file contents or find files by name pattern.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern (content search) or glob pattern (file search)"},
                "target": {"type": "string", "enum": ["content", "files"], "default": "content"},
                "path": {"type": "string", "description": "Directory to search", "default": "/workspace"},
                "file_glob": {"type": "string", "description": "Filter files by glob (e.g. '*.py')"},
                "limit": {"type": "integer", "description": "Max results", "default": 50},
                "context": {"type": "integer", "description": "Context lines around matches", "default": 0},
                "output_mode": {"type": "string", "enum": ["content", "files_only", "count"], "default": "content"},
            },
            "required": ["pattern"],
        },
        handler=search_files,
        toolset="file",
        emoji="🔎",
    )
