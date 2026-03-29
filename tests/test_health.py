"""Health endpoint tests for all three services."""


async def test_agent_health(agent_client):
    resp = await agent_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "agent"}


async def test_tools_health(tools_client):
    resp = await tools_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "tools"}


async def test_sandbox_health(sandbox_client):
    resp = await sandbox_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "sandbox"}
