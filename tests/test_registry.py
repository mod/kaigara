"""Tool registry tests."""

from tools.registry import ToolRegistry


async def _echo_handler(args: dict) -> str:
    return f"echo: {args.get('text', '')}"


async def _failing_handler(args: dict) -> str:
    raise ValueError("something broke")


async def test_register_tool():
    """Register a tool, dispatch by name returns result."""
    reg = ToolRegistry()
    reg.register("echo", "Echo text", {"type": "object"}, _echo_handler)
    result = await reg.dispatch("echo", {"text": "hello"})
    assert result == "echo: hello"


async def test_list_tools():
    """Registered tools appear in schema list."""
    reg = ToolRegistry()
    reg.register("echo", "Echo text", {"type": "object"}, _echo_handler)
    schemas = reg.list_tools()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "echo"
    assert schemas[0]["type"] == "function"


async def test_unknown_tool():
    """Dispatching unknown tool returns error."""
    reg = ToolRegistry()
    result = await reg.dispatch("nonexistent", {})
    assert "unknown tool" in result


async def test_dispatch_error():
    """Handler raising exception returns error message, no crash."""
    reg = ToolRegistry()
    reg.register("fail", "Fails always", {"type": "object"}, _failing_handler)
    result = await reg.dispatch("fail", {})
    assert "error:" in result
    assert "something broke" in result


async def test_tools_endpoint_lists_builtins(tools_client, monkeypatch):
    """GET /tools returns registered built-in tool schemas."""
    # Set a fake API key so web tools pass availability check
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fake-key-for-test")

    resp = await tools_client.get("/tools")
    assert resp.status_code == 200
    tools = resp.json()
    names = {t["function"]["name"] for t in tools}
    assert "read_file" in names
    assert "write_file" in names
    assert "search_files" in names
    assert "web_search" in names
    assert "web_extract" in names
    # New tools
    assert "patch" in names
    assert "terminal" in names
    assert "cronjob" in names


async def test_tool_dispatch_endpoint(tools_client, monkeypatch):
    """POST /tool/{name} dispatches to registered handler."""
    # Override the file_tools sandbox URL to our test sandbox
    import tools.builtins.file_tools as ft
    monkeypatch.setattr(ft, "SANDBOX_URL", "http://testserver-sandbox")

    # We can't actually call sandbox from here without wiring the test client,
    # but we can at least verify the endpoint routes correctly for an unknown tool
    resp = await tools_client.post("/tool/nonexistent", json={})
    assert resp.status_code == 200
    assert "unknown tool" in resp.json()["result"]
