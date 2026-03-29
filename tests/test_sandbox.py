"""Sandbox shell execution tests."""


async def test_exec_echo(sandbox_client):
    resp = await sandbox_client.post("/exec", json={"command": "echo hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["stdout"].strip() == "hello"
    assert data["exit_code"] == 0
    assert data["timed_out"] is False


async def test_exec_env_scrubbed(sandbox_client):
    resp = await sandbox_client.post("/exec", json={"command": "env"})
    data = resp.json()
    env_lines = data["stdout"].strip().splitlines()
    env_keys = {line.split("=", 1)[0] for line in env_lines if "=" in line}
    # Shell auto-adds PWD, SHLVL, _ — these are harmless builtins
    allowed = {"PATH", "HOME", "TERM", "LANG", "PWD", "SHLVL", "_"}
    assert env_keys <= allowed, f"unexpected env vars: {env_keys - allowed}"


async def test_exec_timeout(sandbox_client):
    resp = await sandbox_client.post(
        "/exec", json={"command": "sleep 60", "timeout": 1}
    )
    data = resp.json()
    assert data["timed_out"] is True


async def test_exec_stdout_truncated(sandbox_client):
    # Generate >100KB of output
    resp = await sandbox_client.post(
        "/exec",
        json={"command": "python3 -c \"print('A' * 200_000)\"", "timeout": 10},
    )
    data = resp.json()
    assert len(data["stdout"]) <= 100 * 1024


async def test_exec_workdir(sandbox_client, tmp_path, monkeypatch):
    # Create a subdir in our test workspace
    import sandbox.server as srv

    workspace = srv.WORKSPACE
    subdir = workspace / "subdir"
    subdir.mkdir(exist_ok=True)

    resp = await sandbox_client.post(
        "/exec", json={"command": "pwd", "workdir": "/workspace/subdir"}
    )
    data = resp.json()
    assert data["stdout"].strip().endswith("subdir")
    assert data["exit_code"] == 0


async def test_exec_path_traversal(sandbox_client):
    resp = await sandbox_client.post(
        "/exec", json={"command": "ls", "workdir": "../../etc"}
    )
    assert resp.status_code == 400


async def test_exec_write_read(sandbox_client):
    # Write
    resp = await sandbox_client.post(
        "/exec/write",
        json={"path": "/workspace/test.txt", "content": "hello world"},
    )
    assert resp.status_code == 200

    # Read back
    resp = await sandbox_client.post(
        "/exec/read", json={"path": "/workspace/test.txt"}
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == "hello world"


async def test_exec_no_command(sandbox_client):
    resp = await sandbox_client.post("/exec", json={"command": ""})
    assert resp.status_code == 400


async def test_exec_read_missing_file(sandbox_client):
    resp = await sandbox_client.post(
        "/exec/read", json={"path": "/workspace/nonexistent.txt"}
    )
    assert resp.status_code == 404


async def test_exec_write_path_traversal(sandbox_client):
    resp = await sandbox_client.post(
        "/exec/write",
        json={"path": "../../etc/passwd", "content": "nope"},
    )
    assert resp.status_code == 400


async def test_exec_extra_env(sandbox_client):
    resp = await sandbox_client.post(
        "/exec", json={"command": "echo $MY_VAR", "env": {"MY_VAR": "test123"}}
    )
    data = resp.json()
    assert "test123" in data["stdout"]
