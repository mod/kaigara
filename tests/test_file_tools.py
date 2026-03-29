"""File tools tests — read, write, patch, search via sandbox."""

import json
from unittest.mock import AsyncMock, MagicMock

import httpx

from tools.builtins.file_tools import read_file, write_file, patch, search_files


def _mock_httpx(monkeypatch, response_data: dict, status: int = 200):
    """Patch httpx.AsyncClient inside file_tools to return canned response."""
    mock_resp = httpx.Response(status_code=status, json=response_data)
    mock_post = AsyncMock(return_value=mock_resp)

    mock_client = MagicMock()
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    import tools.builtins.file_tools as ft
    monkeypatch.setattr(ft.httpx, "AsyncClient", lambda: mock_client)
    return mock_post


async def test_read_file(monkeypatch):
    mock_post = _mock_httpx(monkeypatch, {"content": "file content here"})
    result = await read_file({"path": "/workspace/test.txt"})
    data = json.loads(result)
    assert "1\tfile content here" in data["content"]
    assert data["total_lines"] == 1


async def test_read_file_pagination(monkeypatch):
    content = "\n".join(f"line {i}" for i in range(1, 11))
    _mock_httpx(monkeypatch, {"content": content})
    result = await read_file({"path": "/workspace/test.txt", "offset": 3, "limit": 2})
    data = json.loads(result)
    assert "3\tline 3" in data["content"]
    assert "4\tline 4" in data["content"]
    assert data["has_more"] is True
    assert data["next_offset"] == 5


async def test_write_file(monkeypatch):
    mock_post = _mock_httpx(monkeypatch, {"status": "ok"})
    result = await write_file({"path": "/workspace/test.txt", "content": "hello"})
    data = json.loads(result)
    assert data["status"] == "written"


async def test_search_files(monkeypatch):
    mock_post = _mock_httpx(monkeypatch, {"stdout": "file.py:1:match", "stderr": "", "exit_code": 0})
    result = await search_files({"pattern": "match", "path": "/workspace"})
    assert "match" in result


async def test_read_file_not_found(monkeypatch):
    _mock_httpx(monkeypatch, {"detail": "file not found"}, status=404)
    result = await read_file({"path": "/workspace/nope.txt"})
    data = json.loads(result)
    assert "error" in data
