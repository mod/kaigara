"""Slack platform adapter using slack-bolt Socket Mode."""

import asyncio
import logging
import re

from gateway.platforms.base import BasePlatformAdapter, MessageEvent, SendResult

log = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 39000


def _markdown_to_mrkdwn(text: str) -> str:
    """Convert standard markdown to Slack mrkdwn format."""
    # Protect code blocks
    protected = []
    def protect(match):
        protected.append(match.group(0))
        return f"\x00PROTECTED{len(protected)-1}\x00"

    text = re.sub(r"```.*?```", protect, text, flags=re.DOTALL)
    text = re.sub(r"`[^`]+`", protect, text)

    # Convert links: [text](url) → <url|text>
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    # Headers: ## Title → *Title*
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    # Bold: **text** → *text*
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # Italic: *text* → _text_ (careful not to match bold)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"_\1_", text)
    # Strikethrough: ~~text~~ → ~text~
    text = re.sub(r"~~(.+?)~~", r"~\1~", text)

    # Restore protected regions
    for i, p in enumerate(protected):
        text = text.replace(f"\x00PROTECTED{i}\x00", p)

    return text


class SlackAdapter(BasePlatformAdapter):
    """Slack bot adapter using Socket Mode."""

    def __init__(self, config):
        super().__init__("slack", config)
        self._app = None
        self._handler = None
        self._bot_user_id = None
        self._socket_task = None

    async def connect(self) -> bool:
        try:
            from slack_bolt.async_app import AsyncApp
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        except ImportError:
            log.error("slack-bolt not installed")
            return False

        bot_token = self.config.token
        app_token = self.config.extra.get("app_token", "")
        if not bot_token or not app_token:
            log.error("SLACK_BOT_TOKEN and SLACK_APP_TOKEN required")
            return False

        try:
            self._app = AsyncApp(token=bot_token)

            # Get bot user ID
            auth = await self._app.client.auth_test()
            self._bot_user_id = auth.get("user_id")

            # Register event handler
            @self._app.event("message")
            async def handle_message(event, say):
                await self._handle_message_event(event)

            @self._app.command("/kaigara")
            async def handle_command(ack, command):
                await ack()
                await self._handle_slash_command(command)

            # Start Socket Mode
            self._handler = AsyncSocketModeHandler(self._app, app_token)
            self._socket_task = asyncio.create_task(self._handler.start_async())

            self._running = True
            bot_name = auth.get("user", "unknown")
            log.info("Slack connected as @%s (Socket Mode)", bot_name)
            return True

        except Exception as e:
            log.error("Slack connection failed: %s", e)
            return False

    async def disconnect(self):
        if self._handler:
            try:
                await self._handler.close_async()
            except Exception:
                pass
        if self._socket_task:
            self._socket_task.cancel()
        self._running = False

    async def send(self, chat_id: str, content: str, *, reply_to: str = "", thread_id: str = "") -> SendResult:
        if not self._app:
            return SendResult(success=False, error="not connected")

        formatted = _markdown_to_mrkdwn(content)
        chunks = self.truncate_message(formatted, MAX_MESSAGE_LENGTH)

        last_ts = ""
        for chunk in chunks:
            kwargs = {"channel": chat_id, "text": chunk}
            if thread_id:
                kwargs["thread_ts"] = thread_id
            elif reply_to:
                kwargs["thread_ts"] = reply_to

            try:
                resp = await self._app.client.chat_postMessage(**kwargs)
                last_ts = resp.get("ts", "")
            except Exception as e:
                return SendResult(success=False, error=str(e))

        return SendResult(success=True, message_id=last_ts)

    async def send_typing(self, chat_id: str):
        # Slack doesn't have a simple typing indicator
        pass

    async def edit_message(self, chat_id: str, message_id: str, content: str) -> SendResult:
        if not self._app:
            return SendResult(success=False, error="not connected")

        try:
            formatted = _markdown_to_mrkdwn(content)
            await self._app.client.chat_update(
                channel=chat_id,
                ts=message_id,
                text=formatted[:MAX_MESSAGE_LENGTH],
            )
            return SendResult(success=True, message_id=message_id)
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def _handle_message_event(self, event: dict):
        """Handle Slack message events."""
        # Ignore bot messages and message_changed
        if event.get("bot_id") or event.get("subtype"):
            return
        # Ignore own messages
        if event.get("user") == self._bot_user_id:
            return

        user_id = event.get("user", "")
        if not self._is_authorized(user_id):
            return

        text = event.get("text", "")
        # Strip bot mention
        if self._bot_user_id:
            text = re.sub(rf"<@{self._bot_user_id}>\s*", "", text).strip()

        if not text:
            return

        msg_event = MessageEvent(
            text=text,
            platform="slack",
            chat_id=event.get("channel", ""),
            user_id=user_id,
            chat_type="dm" if event.get("channel_type") == "im" else "group",
            thread_id=event.get("thread_ts", ""),
            message_id=event.get("ts", ""),
        )

        if self._message_handler:
            asyncio.create_task(self._message_handler(msg_event))

    async def _handle_slash_command(self, command: dict):
        """Handle /kaigara slash command."""
        user_id = command.get("user_id", "")
        if not self._is_authorized(user_id):
            return

        text = command.get("text", "")
        parts = text.split(maxsplit=1)
        cmd = parts[0] if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        event = MessageEvent(
            text=text,
            platform="slack",
            chat_id=command.get("channel_id", ""),
            user_id=user_id,
            user_name=command.get("user_name", ""),
            is_command=True,
            command=cmd,
            command_args=args,
        )

        if self._message_handler:
            asyncio.create_task(self._message_handler(event))

    def _is_authorized(self, user_id: str) -> bool:
        if not self.config.allowed_users:
            return True
        return user_id in self.config.allowed_users

    def format_message(self, content: str) -> str:
        return _markdown_to_mrkdwn(content)
