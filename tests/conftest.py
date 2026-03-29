"""Shared fixtures for kaigara tests."""

import os
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import sandbox.server as sandbox_srv
from agent.server import app as agent_app
from sandbox.server import app as sandbox_app
from tools.server import app as tools_app


# ---------------------------------------------------------------------------
# Mock environment — ensure agent/sandbox never see real secrets
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_env(monkeypatch, tmp_path):
    """Set env vars for every test. Agent gets only service URLs; tools gets
    a fake API key; sandbox gets nothing secret."""
    monkeypatch.setenv("TOOLS_URL", "http://testserver-tools")
    monkeypatch.setenv("SANDBOX_URL", "http://testserver-sandbox")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-fake-key-do-not-use")

    # Session DB and workspace use isolated temp dirs
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
    monkeypatch.setenv("WORKSPACE", str(workspace_dir))

    # Patch module-level WORKSPACE in sandbox server (resolved at import time)
    monkeypatch.setattr(sandbox_srv, "WORKSPACE", Path(workspace_dir))
    monkeypatch.setattr(sandbox_srv, "SCRUBBED_ENV", {
        **sandbox_srv.SCRUBBED_ENV,
        "HOME": str(workspace_dir),
    })


# ---------------------------------------------------------------------------
# Async HTTP test clients (one per service)
# ---------------------------------------------------------------------------

@pytest.fixture
def agent_client():
    """Async test client for the agent service."""
    transport = ASGITransport(app=agent_app)
    return AsyncClient(transport=transport, base_url="http://testserver-agent")


@pytest.fixture
def tools_client():
    """Async test client for the tools service."""
    transport = ASGITransport(app=tools_app)
    return AsyncClient(transport=transport, base_url="http://testserver-tools")


@pytest.fixture
def sandbox_client():
    """Async test client for the sandbox service."""
    transport = ASGITransport(app=sandbox_app)
    return AsyncClient(transport=transport, base_url="http://testserver-sandbox")
