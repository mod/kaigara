"""Agent conversation loop — runs LLM + tool dispatch until done or budget exhausted."""

import json
import logging

from agent.clients import SandboxClient, ToolsClient
from agent.compression import ContextCompressor
from agent.llm import llm_call
from agent.memory import MemoryStore
from agent.rbac import RBAC, Role
from agent.state import SessionDB

log = logging.getLogger(__name__)

# Tool names that route to sandbox shell execution (legacy — terminal tool now in tools container)
SHELL_TOOLS = {"shell", "bash", "execute_command"}
# Tools handled locally in the agent (not proxied)
LOCAL_TOOLS = {"memory"}

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."

# Tool schemas for tools the agent handles directly (not from tools container)
SHELL_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "terminal",
        "description": "Execute a shell command in the sandbox. Use this to run code, install packages, explore the filesystem, or perform any command-line operation.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "workdir": {"type": "string", "description": "Working directory (default: /workspace)", "default": "/workspace"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30, max: 300)", "default": 30},
            },
            "required": ["command"],
        },
    },
}

MEMORY_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "memory",
        "description": "Persistent memory across sessions. Use to remember facts about the user or project.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "add", "replace", "remove"]},
                "file": {"type": "string", "enum": ["memory", "user"], "default": "memory"},
                "entry": {"type": "string", "description": "Text to add (for 'add' action)"},
                "old_text": {"type": "string", "description": "Text to find (for 'replace' action)"},
                "new_text": {"type": "string", "description": "Replacement text (for 'replace' action)"},
                "text": {"type": "string", "description": "Text to remove (for 'remove' action)"},
            },
            "required": ["action"],
        },
    },
}


class AgentLoop:
    def __init__(
        self,
        tools_client: ToolsClient,
        sandbox_client: SandboxClient,
        model: str = "anthropic/claude-sonnet-4-20250514",
        max_iterations: int = 50,
        session_db: SessionDB | None = None,
        memory: MemoryStore | None = None,
        role: Role = Role.GUEST,
        rbac: RBAC | None = None,
    ):
        self.tools_client = tools_client
        self.sandbox_client = sandbox_client
        self.model = model
        self.max_iterations = max_iterations
        self.session_db = session_db
        self.memory = memory
        self.compressor = ContextCompressor(model)
        self.role = role
        self.rbac = rbac or RBAC()
        self._tool_schemas: list[dict] | None = None

    async def _get_tool_schemas(self) -> list[dict]:
        """Fetch tool schemas from tools container + add local tool schemas."""
        if self._tool_schemas is not None:
            return self._tool_schemas

        schemas = []
        # Fetch remote tools from tools container
        try:
            remote = await self.tools_client.list_tools()
            schemas.extend(remote)
        except Exception:
            log.warning("Failed to fetch tool schemas from tools container")

        # Add shell tool if role allows it
        if self.rbac.can_use_shell(self.role):
            schemas.append(SHELL_TOOL_SCHEMA)

        # Add memory tool
        schemas.append(MEMORY_TOOL_SCHEMA)

        self._tool_schemas = schemas
        return schemas

    async def run(
        self,
        user_message: str,
        conversation_history: list[dict] | None = None,
        system_prompt: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Run conversation until LLM stops calling tools or budget exhausted.

        Returns {"response": str, "messages": list, "tool_calls_made": int, "session_id": str | None}
        """
        messages = list(conversation_history or [])

        # Fetch available tool schemas
        tool_schemas = await self._get_tool_schemas()

        # Build system prompt with memory snapshot
        full_prompt = system_prompt or ""
        if self.memory:
            snapshot = self.memory.snapshot()
            if snapshot:
                full_prompt = f"{full_prompt}\n\n{snapshot}" if full_prompt else snapshot

        if full_prompt and (not messages or messages[0].get("role") != "system"):
            messages.insert(0, {"role": "system", "content": full_prompt})

        # Create session if DB available
        if self.session_db and not session_id:
            session_id = self.session_db.create_session(
                model=self.model, system_prompt=system_prompt or ""
            )

        # Add user message
        messages.append({"role": "user", "content": user_message})
        if self.session_db and session_id:
            self.session_db.add_message(session_id, "user", user_message)

        tool_calls_made = 0

        for _iteration in range(self.max_iterations):
            # Compress if approaching context limit
            if self.compressor.needs_compression(messages):
                messages = await self.compressor.compress(
                    messages, llm_call
                )

            # Call LLM provider directly
            llm_request: dict = {
                "model": self.model,
                "messages": messages,
                "max_tokens": 4096,
                "temperature": 0.7,
            }
            if tool_schemas:
                llm_request["tools"] = tool_schemas
            try:
                llm_response = await llm_call(llm_request)
            except RuntimeError as e:
                assistant_msg = {"role": "assistant", "content": str(e)}
                messages.append(assistant_msg)
                break

            # Extract assistant message from response (OpenAI-compatible format)
            assistant_msg = self._extract_assistant_message(llm_response)
            messages.append(assistant_msg)

            if self.session_db and session_id:
                self.session_db.add_message(
                    session_id,
                    "assistant",
                    content=assistant_msg.get("content"),
                    tool_calls=assistant_msg.get("tool_calls"),
                )

            # Check for tool calls
            tool_calls = assistant_msg.get("tool_calls", [])
            if not tool_calls:
                # No tool calls — LLM is done
                break

            # Dispatch each tool call
            for tool_call in tool_calls:
                tool_calls_made += 1
                tool_name = tool_call["function"]["name"]
                try:
                    tool_args = json.loads(tool_call["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    tool_args = {}

                result = await self._dispatch_tool(tool_name, tool_args)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": result,
                })
                if self.session_db and session_id:
                    self.session_db.add_message(
                        session_id, "tool", content=result,
                        tool_call_id=tool_call["id"],
                    )

        # Extract final text response
        final_response = self._get_text_content(assistant_msg)

        # Close session
        if self.session_db and session_id:
            self.session_db.close_session(session_id)

        return {
            "response": final_response,
            "messages": messages,
            "tool_calls_made": tool_calls_made,
            "session_id": session_id,
        }

    async def _dispatch_tool(self, name: str, args: dict) -> str:
        """Route tool call to sandbox, local handler, or tools container.
        Enforces RBAC before dispatch."""
        # RBAC: check shell access
        if name in SHELL_TOOLS:
            if not self.rbac.can_use_shell(self.role):
                return json.dumps({"error": "shell access denied for your role"})
            command = args.get("command", "")
            workdir = args.get("workdir", "/workspace")
            timeout = args.get("timeout", 30)
            result = await self.sandbox_client.exec(command, workdir, timeout)
            return json.dumps(result)

        # RBAC: check tool access
        if not self.rbac.can_use_tool(self.role, name):
            return json.dumps({"error": f"tool '{name}' not available for your role"})

        if name in LOCAL_TOOLS:
            return self._handle_local_tool(name, args)
        else:
            result = await self.tools_client.tool(name, args)
            return json.dumps(result)

    def _handle_local_tool(self, name: str, args: dict) -> str:
        """Handle tools that run inside the agent process."""
        if name == "memory" and self.memory:
            action = args.get("action", "read")
            file = args.get("file", "memory")

            if action == "read":
                return self.memory.read(file) or "(empty)"
            elif action == "add":
                err = self.memory.add(args.get("entry", ""), file)
                return err or "saved"
            elif action == "replace":
                err = self.memory.replace(
                    args.get("old_text", ""), args.get("new_text", ""), file
                )
                return err or "replaced"
            elif action == "remove":
                err = self.memory.remove(args.get("text", ""), file)
                return err or "removed"
            else:
                return f"unknown memory action: {action}"
        return f"unknown local tool: {name}"

    def _extract_assistant_message(self, llm_response: dict) -> dict:
        """Extract the assistant message from an OpenAI-compatible response."""
        choices = llm_response.get("choices", [])
        if choices:
            return choices[0].get("message", {"role": "assistant", "content": ""})
        return {"role": "assistant", "content": ""}

    def _get_text_content(self, message: dict) -> str:
        """Get text content from a message."""
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        # Handle list-format content (Anthropic style)
        if isinstance(content, list):
            return "".join(
                block.get("text", "") for block in content if block.get("type") == "text"
            )
        return str(content) if content else ""
