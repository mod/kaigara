"""Base platform adapter — abstract interface for messaging platforms."""

import abc
import logging
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)


@dataclass
class MessageEvent:
    """Normalized incoming message from any platform."""
    text: str
    platform: str
    chat_id: str
    user_id: str
    user_name: str = ""
    chat_name: str = ""
    chat_type: str = "dm"
    thread_id: str = ""
    message_id: str = ""
    is_command: bool = False
    command: str = ""
    command_args: str = ""
    timestamp: datetime | None = None


@dataclass
class SendResult:
    """Result of sending a message."""
    success: bool
    message_id: str = ""
    error: str = ""


class BasePlatformAdapter(abc.ABC):
    """Base class for all messaging platform adapters."""

    def __init__(self, platform: str, config):
        self.platform = platform
        self.config = config
        self._message_handler = None
        self._running = False

    def set_message_handler(self, handler):
        """Set the callback for incoming messages."""
        self._message_handler = handler

    @abc.abstractmethod
    async def connect(self) -> bool:
        """Connect to the platform. Returns True on success."""
        ...

    @abc.abstractmethod
    async def disconnect(self):
        """Disconnect from the platform."""
        ...

    @abc.abstractmethod
    async def send(self, chat_id: str, content: str, *, reply_to: str = "", thread_id: str = "") -> SendResult:
        """Send a message to a chat."""
        ...

    async def send_typing(self, chat_id: str):
        """Send typing indicator (optional)."""
        pass

    async def edit_message(self, chat_id: str, message_id: str, content: str) -> SendResult:
        """Edit a sent message (optional)."""
        return SendResult(success=False, error="not supported")

    def format_message(self, content: str) -> str:
        """Convert markdown to platform-specific format. Override in subclasses."""
        return content

    @staticmethod
    def truncate_message(content: str, max_len: int = 4096) -> list[str]:
        """Split long messages into chunks."""
        if len(content) <= max_len:
            return [content]

        chunks = []
        while content:
            if len(content) <= max_len:
                chunks.append(content)
                break

            # Try to split at newline
            split_at = content.rfind("\n", 0, max_len)
            if split_at < max_len // 2:
                split_at = max_len

            chunks.append(content[:split_at])
            content = content[split_at:].lstrip("\n")

        return chunks
