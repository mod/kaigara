"""Kaigara console chat — interactive REPL that talks to the running agent stack."""

import argparse
import asyncio
import json
import sys

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

console = Console()

# ── Key bindings: Enter submits, Alt+Enter / Ctrl+J inserts newline ──────────

bindings = KeyBindings()


@bindings.add(Keys.Enter)
def _submit(event):
    event.current_buffer.validate_and_handle()


@bindings.add(Keys.Escape, Keys.Enter)
@bindings.add(Keys.ControlJ)
def _newline(event):
    event.current_buffer.insert_text("\n")


# ── Slash commands ───────────────────────────────────────────────────────────

HELP_TEXT = """\
[bold]Commands:[/]
  /new           Start a new session
  /sessions      List recent sessions
  /history       Show current session messages
  /model <name>  Switch model (e.g. /model anthropic/claude-sonnet-4-20250514)
  /role <role>   Switch role (owner, member, guest)
  /stream        Toggle streaming mode
  /help          Show this help
  /exit, /quit   Exit

[dim]Alt+Enter or Ctrl+J for multiline input[/]"""


class KaigaraCLI:
    def __init__(self, base_url: str, model: str, role: str, stream: bool):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.role = role
        self.stream = stream
        self.session_id: str | None = None
        self.prompt_session = PromptSession(
            history=InMemoryHistory(),
            key_bindings=bindings,
            multiline=True,
        )

    # ── Chat ─────────────────────────────────────────────────────────────

    async def send_message(self, text: str):
        body = {
            "message": text,
            "model": self.model,
            "role": self.role,
        }
        if self.session_id:
            body["session_id"] = self.session_id

        if self.stream:
            await self._send_stream(body)
        else:
            await self._send_sync(body)

    async def _send_sync(self, body: dict):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/chat", json=body, timeout=120
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError:
            console.print("[red]Connection refused — are the containers running? (make up)[/]")
            return
        except httpx.HTTPStatusError as e:
            console.print(f"[red]HTTP {e.response.status_code}:[/] {e.response.text[:500]}")
            return
        except httpx.ReadTimeout:
            console.print("[red]Request timed out[/]")
            return

        self.session_id = data.get("session_id")
        tool_calls = data.get("tool_calls_made", 0)
        response = data.get("response", "")

        if tool_calls:
            console.print(f"[dim]({tool_calls} tool call{'s' if tool_calls != 1 else ''})[/]")
        console.print(Panel(Markdown(response), border_style="blue", padding=(0, 1)))

    async def _send_stream(self, body: dict):
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST", f"{self.base_url}/chat/stream", json=body, timeout=120
                ) as resp:
                    resp.raise_for_status()
                    chunks: list[str] = []
                    tool_calls = 0

                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            payload = line[6:]
                            try:
                                event_data = json.loads(payload)
                            except json.JSONDecodeError:
                                continue

                            if "text" in event_data:
                                chunk = event_data["text"]
                                chunks.append(chunk)
                                # Print token-by-token to terminal
                                print(chunk, end="", flush=True)
                            elif "session_id" in event_data:
                                self.session_id = event_data.get("session_id")
                                tool_calls = event_data.get("tool_calls_made", 0)

                    print()  # newline after streaming
                    if tool_calls:
                        console.print(f"[dim]({tool_calls} tool call{'s' if tool_calls != 1 else ''})[/]")

        except httpx.ConnectError:
            console.print("[red]Connection refused — are the containers running? (make up)[/]")
        except httpx.HTTPStatusError as e:
            console.print(f"[red]HTTP {e.response.status_code}[/]")

    # ── Slash commands ───────────────────────────────────────────────────

    async def handle_command(self, text: str) -> bool:
        """Handle slash command. Returns True if handled."""
        parts = text.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit"):
            return False  # signal exit

        elif cmd == "/help":
            console.print(HELP_TEXT)

        elif cmd == "/new":
            self.session_id = None
            console.print("[green]New session started.[/]")

        elif cmd == "/model":
            if arg:
                self.model = arg
                console.print(f"[green]Model set to {self.model}[/]")
            else:
                console.print(f"Current model: [bold]{self.model}[/]")

        elif cmd == "/role":
            if arg and arg in ("owner", "member", "guest"):
                self.role = arg
                console.print(f"[green]Role set to {self.role}[/]")
            else:
                console.print(f"Current role: [bold]{self.role}[/]  (owner, member, guest)")

        elif cmd == "/stream":
            self.stream = not self.stream
            console.print(f"[green]Streaming {'on' if self.stream else 'off'}[/]")

        elif cmd == "/sessions":
            await self._list_sessions()

        elif cmd == "/history":
            await self._show_history()

        else:
            console.print(f"[yellow]Unknown command: {cmd}[/]  (try /help)")

        return True  # continue

    async def _list_sessions(self):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.base_url}/sessions?limit=10", timeout=10)
                resp.raise_for_status()
                sessions = resp.json()
        except httpx.ConnectError:
            console.print("[red]Connection refused[/]")
            return

        if not sessions:
            console.print("[dim]No sessions yet.[/]")
            return

        for s in sessions:
            sid = s.get("id", "?")[:16]
            model = s.get("model", "?")
            msgs = s.get("message_count", 0)
            started = s.get("started_at", "")[:19]
            console.print(f"  [cyan]{sid}[/]  {model}  {msgs} msgs  {started}")

    async def _show_history(self):
        if not self.session_id:
            console.print("[dim]No active session.[/]")
            return
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/sessions/{self.session_id}", timeout=10
                )
                resp.raise_for_status()
                session = resp.json()
        except httpx.ConnectError:
            console.print("[red]Connection refused[/]")
            return

        for msg in session.get("messages", []):
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if not content:
                continue
            color = {"user": "green", "assistant": "blue", "system": "yellow"}.get(role, "white")
            console.print(f"[bold {color}]{role}:[/] {content[:200]}")

    # ── Main REPL ────────────────────────────────────────────────────────

    async def run(self):
        console.print(
            Panel(
                "[bold]kaigara[/] console\n"
                f"[dim]connected to {self.base_url}  |  model: {self.model}  |  role: {self.role}[/]\n"
                "[dim]type /help for commands, Alt+Enter for newline[/]",
                border_style="blue",
                padding=(0, 1),
            )
        )

        # Quick health check
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.base_url}/health", timeout=5)
                resp.raise_for_status()
                console.print("[green]agent is up[/]\n")
        except Exception:
            console.print("[yellow]warning: agent not reachable at {self.base_url} — start with: make up[/]\n")

        while True:
            try:
                prompt_text = HTML(
                    f"<ansiblue>kaigara</ansiblue>"
                    f"<ansigray>({self.role})</ansigray>"
                    f"<ansiblue> > </ansiblue>"
                )
                text = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.prompt_session.prompt(prompt_text)
                )
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye[/]")
                break

            text = text.strip()
            if not text:
                continue

            if text.startswith("/"):
                should_continue = await self.handle_command(text)
                if not should_continue:
                    console.print("[dim]bye[/]")
                    break
            else:
                await self.send_message(text)


def main():
    parser = argparse.ArgumentParser(description="Kaigara console chat")
    parser.add_argument("--url", default="http://localhost:8080", help="Agent URL")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4-20250514", help="LLM model")
    parser.add_argument("--role", default="owner", choices=["owner", "member", "guest"], help="RBAC role")
    parser.add_argument("--no-stream", action="store_true", help="Disable streaming")
    args = parser.parse_args()

    cli = KaigaraCLI(
        base_url=args.url,
        model=args.model,
        role=args.role,
        stream=not args.no_stream,
    )
    try:
        asyncio.run(cli.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
