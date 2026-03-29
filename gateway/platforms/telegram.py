"""Telegram platform adapter."""

import asyncio
import logging
import re

from gateway.platforms.base import BasePlatformAdapter, MessageEvent, SendResult

log = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4096

# MarkdownV2 special chars that need escaping
_MDV2_ESCAPE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _escape_mdv2(text: str) -> str:
    """Escape MarkdownV2 special characters."""
    return _MDV2_ESCAPE.sub(r"\\\1", text)


def _markdown_to_mdv2(text: str) -> str:
    """Convert standard markdown to Telegram MarkdownV2."""
    # Protect code blocks
    parts = []
    last_end = 0
    for match in re.finditer(r"```(\w*)\n(.*?)```", text, re.DOTALL):
        # Escape text before code block
        before = text[last_end:match.start()]
        parts.append(_escape_mdv2(before))
        # Code block — only escape backslash and backtick
        lang = match.group(1)
        code = match.group(2).replace("\\", "\\\\").replace("`", "\\`")
        parts.append(f"```{lang}\n{code}```")
        last_end = match.end()

    remaining = text[last_end:]

    # Handle inline code
    inline_parts = []
    il_last = 0
    for match in re.finditer(r"`([^`]+)`", remaining):
        before = remaining[il_last:match.start()]
        inline_parts.append(_escape_mdv2(before))
        code = match.group(1).replace("\\", "\\\\").replace("`", "\\`")
        inline_parts.append(f"`{code}`")
        il_last = match.end()
    inline_parts.append(_escape_mdv2(remaining[il_last:]))
    parts.append("".join(inline_parts))

    result = "".join(parts)

    # Bold: **text** → *text*  (already escaped the inner *)
    result = re.sub(r"\\\*\\\*(.*?)\\\*\\\*", r"*\1*", result)
    # Italic: *text* → _text_
    result = re.sub(r"\\\*(.*?)\\\*", r"_\1_", result)

    return result


class TelegramAdapter(BasePlatformAdapter):
    """Telegram bot adapter using python-telegram-bot."""

    def __init__(self, config):
        super().__init__("telegram", config)
        self._app = None
        self._bot = None

    async def connect(self) -> bool:
        try:
            from telegram import Bot, Update
            from telegram.ext import Application, MessageHandler, CommandHandler, filters
        except ImportError:
            log.error("python-telegram-bot not installed")
            return False

        token = self.config.token
        if not token:
            log.error("TELEGRAM_BOT_TOKEN not set")
            return False

        try:
            self._app = Application.builder().token(token).build()
            self._bot = self._app.bot

            # Register handlers
            self._app.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND, self._handle_text
            ))
            self._app.add_handler(CommandHandler("start", self._handle_command))
            self._app.add_handler(CommandHandler("new", self._handle_command))
            self._app.add_handler(CommandHandler("help", self._handle_command))

            # Start polling
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(drop_pending_updates=True)

            me = await self._bot.get_me()
            self._running = True
            log.info("Telegram connected as @%s", me.username)
            return True

        except Exception as e:
            log.error("Telegram connection failed: %s", e)
            return False

    async def disconnect(self):
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception:
                pass
        self._running = False

    async def send(self, chat_id: str, content: str, *, reply_to: str = "", thread_id: str = "") -> SendResult:
        if not self._bot:
            return SendResult(success=False, error="not connected")

        formatted = _markdown_to_mdv2(content)
        chunks = self.truncate_message(formatted, MAX_MESSAGE_LENGTH)

        last_msg_id = ""
        for chunk in chunks:
            kwargs = {"chat_id": int(chat_id), "text": chunk}
            if reply_to:
                kwargs["reply_to_message_id"] = int(reply_to)
            if thread_id:
                kwargs["message_thread_id"] = int(thread_id)

            try:
                msg = await self._bot.send_message(
                    parse_mode="MarkdownV2", **kwargs
                )
                last_msg_id = str(msg.message_id)
            except Exception:
                # Fallback to plain text
                try:
                    kwargs["text"] = content[:MAX_MESSAGE_LENGTH] if chunk == chunks[0] else chunk
                    msg = await self._bot.send_message(**kwargs)
                    last_msg_id = str(msg.message_id)
                except Exception as e:
                    return SendResult(success=False, error=str(e))

        return SendResult(success=True, message_id=last_msg_id)

    async def send_typing(self, chat_id: str):
        if self._bot:
            try:
                await self._bot.send_chat_action(chat_id=int(chat_id), action="typing")
            except Exception:
                pass

    async def edit_message(self, chat_id: str, message_id: str, content: str) -> SendResult:
        if not self._bot:
            return SendResult(success=False, error="not connected")

        try:
            formatted = _markdown_to_mdv2(content)
            await self._bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=formatted[:MAX_MESSAGE_LENGTH],
                parse_mode="MarkdownV2",
            )
            return SendResult(success=True, message_id=message_id)
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return SendResult(success=True, message_id=message_id)
            return SendResult(success=False, error=str(e))

    async def _handle_text(self, update, context):
        """Handle incoming text messages."""
        if not update.message or not update.message.text:
            return
        if not self._is_authorized(update.message.from_user):
            return

        event = MessageEvent(
            text=update.message.text,
            platform="telegram",
            chat_id=str(update.message.chat_id),
            user_id=str(update.message.from_user.id),
            user_name=update.message.from_user.first_name or "",
            chat_name=update.message.chat.title or "",
            chat_type="dm" if update.message.chat.type == "private" else "group",
            thread_id=str(update.message.message_thread_id) if update.message.message_thread_id else "",
            message_id=str(update.message.message_id),
        )

        if self._message_handler:
            asyncio.create_task(self._message_handler(event))

    async def _handle_command(self, update, context):
        """Handle slash commands."""
        if not update.message:
            return
        if not self._is_authorized(update.message.from_user):
            return

        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        command = parts[0].lstrip("/").split("@")[0]
        args = parts[1] if len(parts) > 1 else ""

        event = MessageEvent(
            text=text,
            platform="telegram",
            chat_id=str(update.message.chat_id),
            user_id=str(update.message.from_user.id),
            user_name=update.message.from_user.first_name or "",
            chat_type="dm" if update.message.chat.type == "private" else "group",
            message_id=str(update.message.message_id),
            is_command=True,
            command=command,
            command_args=args,
        )

        if self._message_handler:
            asyncio.create_task(self._message_handler(event))

    def _is_authorized(self, user) -> bool:
        """Check if user is in allowed list."""
        if not self.config.allowed_users:
            return True  # No allowlist = allow all
        return str(user.id) in self.config.allowed_users

    def format_message(self, content: str) -> str:
        return _markdown_to_mdv2(content)
