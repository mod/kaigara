"""Gateway runner — bridges messaging platforms to kaigara agent."""

import asyncio
import logging
import os

import httpx

from gateway.config import GatewayConfig, Platform
from gateway.session import SessionStore, SessionSource, build_session_key
from gateway.platforms.base import MessageEvent

log = logging.getLogger(__name__)


class GatewayRunner:
    """Main gateway process — connects platforms and routes messages to agent."""

    def __init__(self, config: GatewayConfig | None = None):
        self.config = config or GatewayConfig.from_env()
        self.session_store = SessionStore(self.config.data_dir)
        self._adapters: dict[Platform, object] = {}
        self._running_agents: dict[str, bool] = {}  # session_key -> is_running
        self._cron_task: asyncio.Task | None = None

    async def start(self) -> bool:
        """Initialize and connect all configured platform adapters."""
        connected = 0

        for platform, pconfig in self.config.platforms.items():
            if not pconfig.enabled:
                continue

            adapter = self._create_adapter(platform, pconfig)
            if adapter is None:
                continue

            adapter.set_message_handler(self._handle_message)

            try:
                if await adapter.connect():
                    self._adapters[platform] = adapter
                    connected += 1
                else:
                    log.error("Failed to connect %s", platform)
            except Exception as e:
                log.error("Failed to connect %s: %s", platform, e)

        if connected == 0:
            log.error("No platforms connected")
            return False

        log.info("Gateway started with %d platform(s)", connected)

        # Start cron scheduler if available
        try:
            from cron.scheduler import start_scheduler
            self._cron_task = asyncio.create_task(start_scheduler())
        except ImportError:
            pass

        return True

    async def stop(self):
        """Disconnect all platforms."""
        if self._cron_task:
            self._cron_task.cancel()

        for platform, adapter in self._adapters.items():
            try:
                await adapter.disconnect()
                log.info("Disconnected %s", platform)
            except Exception as e:
                log.error("Error disconnecting %s: %s", platform, e)

    async def _handle_message(self, event: MessageEvent):
        """Process incoming message from any platform."""
        # Build session
        source = SessionSource(
            platform=event.platform,
            chat_id=event.chat_id,
            user_id=event.user_id,
            user_name=event.user_name,
            chat_name=event.chat_name,
            chat_type=event.chat_type,
            thread_id=event.thread_id,
        )
        session = self.session_store.get_or_create(source)
        session_key = session.key

        # Handle commands
        if event.is_command:
            await self._handle_command(event, session)
            return

        # Check if agent already running for this session
        if self._running_agents.get(session_key):
            log.info("Agent already running for session %s — queuing message", session_key)
            return

        # Send typing indicator
        adapter = self._adapters.get(Platform(event.platform))
        if adapter:
            await adapter.send_typing(event.chat_id)

        # Run agent
        self._running_agents[session_key] = True
        try:
            response = await self._run_agent(event, session)

            if response and adapter:
                await adapter.send(
                    event.chat_id,
                    response,
                    reply_to=event.message_id,
                    thread_id=event.thread_id,
                )
        except Exception as e:
            log.error("Agent error for session %s: %s", session_key, e)
            if adapter:
                await adapter.send(event.chat_id, f"Error: {e}")
        finally:
            self._running_agents[session_key] = False

    async def _run_agent(self, event: MessageEvent, session) -> str:
        """Call kaigara agent /chat API."""
        payload = {
            "message": event.text,
            "role": self.config.role,
        }
        if self.config.model:
            payload["model"] = self.config.model
        if session.session_id:
            payload["session_id"] = session.session_id

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.config.agent_url}/chat",
                json=payload,
                timeout=300,
            )
            data = resp.json()

        # Store session ID for continuity
        agent_session_id = data.get("session_id")
        if agent_session_id:
            self.session_store.update_session_id(session.key, agent_session_id)

        return data.get("response", "")

    async def _handle_command(self, event: MessageEvent, session):
        """Handle gateway commands (/new, /help, etc.)."""
        adapter = self._adapters.get(Platform(event.platform))
        if not adapter:
            return

        cmd = event.command.lower()

        if cmd in ("new", "reset"):
            self.session_store.reset_session(session.key)
            await adapter.send(event.chat_id, "Session reset. Starting fresh conversation.")

        elif cmd == "help":
            help_text = (
                "**Kaigara Gateway Commands**\n\n"
                "/new — Start a new conversation\n"
                "/help — Show this help\n"
                "/start — Welcome message\n"
            )
            await adapter.send(event.chat_id, help_text)

        elif cmd == "start":
            await adapter.send(event.chat_id, "Hello! I'm Kaigara. Send me a message to start chatting.")

        else:
            # Treat unknown commands as regular messages
            event.is_command = False
            event.text = f"/{event.command} {event.command_args}".strip()
            await self._handle_message(event)

    def _create_adapter(self, platform: Platform, config):
        """Factory for platform adapters."""
        if platform == Platform.TELEGRAM:
            from gateway.platforms.telegram import TelegramAdapter
            return TelegramAdapter(config)
        elif platform == Platform.SLACK:
            from gateway.platforms.slack import SlackAdapter
            return SlackAdapter(config)
        else:
            log.warning("Unknown platform: %s", platform)
            return None


async def run_gateway():
    """Entry point for running the gateway."""
    config = GatewayConfig.from_env()
    runner = GatewayRunner(config)

    if not await runner.start():
        log.error("Gateway failed to start")
        return

    # Keep running until interrupted
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await runner.stop()


def main():
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )
    asyncio.run(run_gateway())
