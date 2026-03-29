"""RBAC tests."""

import json
from unittest.mock import AsyncMock

from agent.clients import SandboxClient, ToolsClient
from agent.loop import AgentLoop
from agent.rbac import RBAC, Role


def _llm_tool_call(tool_name: str, args: dict) -> dict:
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps(args)},
                }],
            }
        }]
    }


def _llm_text(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _make_loop(role: Role, llm_responses, tool_mock=None, exec_mock=None) -> AgentLoop:
    tc = ToolsClient("http://fake")
    sc = SandboxClient("http://fake")
    if tool_mock:
        tc.tool = tool_mock
    if exec_mock:
        sc.exec = exec_mock
    import agent.loop as _loop
    _loop.llm_call = AsyncMock(side_effect=llm_responses)
    return AgentLoop(tools_client=tc, sandbox_client=sc, role=role)


def test_owner_full_access():
    rbac = RBAC()
    assert rbac.can_use_shell(Role.OWNER)
    assert rbac.can_use_tool(Role.OWNER, "web_search")
    assert rbac.can_use_tool(Role.OWNER, "write_file")
    assert rbac.can_use_tool(Role.OWNER, "anything")


def test_member_shell_access():
    rbac = RBAC()
    assert rbac.can_use_shell(Role.MEMBER)


def test_guest_no_shell():
    rbac = RBAC()
    assert not rbac.can_use_shell(Role.GUEST)


def test_guest_restricted_tools():
    rbac = RBAC()
    assert rbac.can_use_tool(Role.GUEST, "web_search")
    assert rbac.can_use_tool(Role.GUEST, "read_file")
    assert not rbac.can_use_tool(Role.GUEST, "write_file")
    assert not rbac.can_use_tool(Role.GUEST, "search_files")


def test_guest_token_limit():
    rbac = RBAC()
    assert rbac.max_tokens(Role.GUEST) < rbac.max_tokens(Role.MEMBER)
    assert rbac.max_tokens(Role.MEMBER) < rbac.max_tokens(Role.OWNER)


def test_guest_output_filtered():
    rbac = RBAC()
    assert rbac.should_filter_output(Role.GUEST)
    assert not rbac.should_filter_output(Role.MEMBER)
    assert not rbac.should_filter_output(Role.OWNER)


def test_default_role_guest():
    """Request without explicit role defaults to guest (verified in ChatRequest)."""
    from agent.server import ChatRequest
    req = ChatRequest(message="hi")
    assert req.role == "guest"


async def test_guest_shell_denied():
    """Guest terminal request returns error — tool not in GUEST_TOOLS."""
    mock_exec = AsyncMock()
    loop = _make_loop(
        Role.GUEST,
        [_llm_tool_call("terminal", {"command": "ls"}), _llm_text("ok")],
        exec_mock=mock_exec,
    )
    result = await loop.run("list files")
    # Sandbox should never be called
    mock_exec.assert_not_called()
    # The tool result should contain an error (denied or not available)
    tool_msgs = [m for m in result["messages"] if m.get("role") == "tool"]
    assert any("not available" in m["content"] or "denied" in m["content"] for m in tool_msgs)


async def test_owner_shell_allowed():
    """Owner can use terminal tool (routes to tools container)."""
    mock_tool = AsyncMock(return_value={"result": '{"stdout":"ok","stderr":"","exit_code":0}'})
    loop = _make_loop(
        Role.OWNER,
        [_llm_tool_call("terminal", {"command": "ls"}), _llm_text("done")],
        tool_mock=mock_tool,
    )
    await loop.run("list files")
    mock_tool.assert_called_once()


async def test_guest_blocked_tool():
    """Guest cannot use write_file tool."""
    mock_tool = AsyncMock(return_value={"result": "written"})
    loop = _make_loop(
        Role.GUEST,
        [_llm_tool_call("write_file", {"path": "x", "content": "y"}), _llm_text("ok")],
        tool_mock=mock_tool,
    )
    result = await loop.run("write file")
    mock_tool.assert_not_called()
    tool_msgs = [m for m in result["messages"] if m.get("role") == "tool"]
    assert any("not available" in m["content"] for m in tool_msgs)
