"""Agent conversation loop tests."""

import json
from unittest.mock import AsyncMock

from agent.clients import SandboxClient, ToolsClient
from agent.loop import AgentLoop
from agent.rbac import Role


def _llm_text_response(text: str) -> dict:
    """Simulate an OpenAI-compatible response with just text."""
    return {
        "choices": [
            {"message": {"role": "assistant", "content": text}}
        ]
    }


def _llm_tool_call_response(tool_name: str, args: dict, call_id: str = "call_1") -> dict:
    """Simulate a response with a tool call."""
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(args),
                            },
                        }
                    ],
                }
            }
        ]
    }


def _make_loop(llm_mock, tools_tool_mock=None, sandbox_exec_mock=None, role=Role.OWNER) -> AgentLoop:
    tools_client = ToolsClient("http://fake-tools")
    sandbox_client = SandboxClient("http://fake-sandbox")
    if tools_tool_mock:
        tools_client.tool = tools_tool_mock
    if sandbox_exec_mock:
        sandbox_client.exec = sandbox_exec_mock
    loop = AgentLoop(tools_client=tools_client, sandbox_client=sandbox_client, role=role)
    # Patch the direct LLM call used by the agent loop
    import agent.loop
    agent.loop.llm_call = llm_mock
    return loop


async def test_simple_conversation():
    """User message gets LLM response with no tool calls."""
    mock_llm = AsyncMock(return_value=_llm_text_response("Hello back!"))
    loop = _make_loop(mock_llm)

    result = await loop.run("Hello")
    assert result["response"] == "Hello back!"
    assert result["tool_calls_made"] == 0
    assert len(result["messages"]) >= 2  # system + user + assistant


async def test_tool_call_dispatch():
    """LLM returns tool_call, agent dispatches to tools container and loops."""
    # First call: LLM requests a tool call
    # Second call: LLM responds with text after seeing tool result
    mock_llm = AsyncMock(
        side_effect=[
            _llm_tool_call_response("web_search", {"query": "test"}),
            _llm_text_response("Found results!"),
        ]
    )
    mock_tool = AsyncMock(return_value={"results": ["result1"]})
    loop = _make_loop(mock_llm, tools_tool_mock=mock_tool)

    result = await loop.run("Search for test")
    assert result["response"] == "Found results!"
    assert result["tool_calls_made"] == 1
    mock_tool.assert_called_once_with("web_search", {"query": "test"})


async def test_shell_tool_dispatch():
    """tool_call for 'terminal' now routes to tools container (terminal tool lives there)."""
    mock_llm = AsyncMock(
        side_effect=[
            _llm_tool_call_response("terminal", {"command": "ls"}),
            _llm_text_response("Listed files."),
        ]
    )
    mock_tool = AsyncMock(return_value={"result": '{"stdout":"file.txt\\n","stderr":"","exit_code":0}'})
    mock_exec = AsyncMock()
    loop = _make_loop(mock_llm, tools_tool_mock=mock_tool, sandbox_exec_mock=mock_exec)

    result = await loop.run("List files")
    assert result["tool_calls_made"] == 1
    mock_tool.assert_called_once()
    mock_exec.assert_not_called()


async def test_shell_tool_names():
    """All shell tool name variants route to sandbox."""
    for tool_name in ["shell", "bash", "execute_command"]:
        mock_llm = AsyncMock(
            side_effect=[
                _llm_tool_call_response(tool_name, {"command": "echo hi"}),
                _llm_text_response("Done."),
            ]
        )
        mock_exec = AsyncMock(return_value={"stdout": "hi\n", "stderr": "", "exit_code": 0})
        loop = _make_loop(mock_llm, sandbox_exec_mock=mock_exec)

        result = await loop.run("run something")
        assert result["tool_calls_made"] == 1
        mock_exec.assert_called_once()


async def test_max_iterations():
    """Loop stops after max_iterations even if LLM keeps calling tools."""
    # LLM always returns a tool call — should be capped at 3
    mock_llm = AsyncMock(
        return_value=_llm_tool_call_response("some_tool", {"arg": "val"})
    )
    mock_tool = AsyncMock(return_value={"result": "ok"})
    loop = _make_loop(mock_llm, tools_tool_mock=mock_tool)
    loop.max_iterations = 3

    result = await loop.run("Do something forever")
    assert result["tool_calls_made"] == 3
    assert mock_llm.call_count == 3


async def test_conversation_history():
    """Previous messages are preserved across turns."""
    mock_llm = AsyncMock(return_value=_llm_text_response("I remember!"))
    loop = _make_loop(mock_llm)

    history = [
        {"role": "user", "content": "My name is Alice"},
        {"role": "assistant", "content": "Hello Alice!"},
    ]
    result = await loop.run("What's my name?", conversation_history=history)

    # History should contain: original history + new user msg + assistant response
    messages = result["messages"]
    assert messages[0]["content"] == "My name is Alice"
    assert messages[1]["content"] == "Hello Alice!"
    assert messages[2]["content"] == "What's my name?"


async def test_system_prompt():
    """System prompt is included as first message."""
    mock_llm = AsyncMock(return_value=_llm_text_response("Arrr!"))
    loop = _make_loop(mock_llm)

    result = await loop.run("Hello", system_prompt="You are a pirate.")
    messages = result["messages"]
    assert messages[0] == {"role": "system", "content": "You are a pirate."}
