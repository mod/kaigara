"""Gateway configuration."""

import os
import logging
from dataclasses import dataclass, field
from enum import StrEnum

log = logging.getLogger(__name__)


class Platform(StrEnum):
    TELEGRAM = "telegram"
    SLACK = "slack"


@dataclass
class PlatformConfig:
    enabled: bool = False
    token: str = ""
    home_channel: str = ""
    allowed_users: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


@dataclass
class GatewayConfig:
    """Main gateway configuration."""
    agent_url: str = "http://localhost:8080"
    role: str = "owner"
    model: str | None = None
    platforms: dict[Platform, PlatformConfig] = field(default_factory=dict)
    session_idle_minutes: int = 1440  # 24h default
    data_dir: str = ""

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        """Build config from environment variables."""
        config = cls(
            agent_url=os.environ.get("AGENT_URL", "http://localhost:8080"),
            role=os.environ.get("GATEWAY_ROLE", "owner"),
            model=os.environ.get("GATEWAY_MODEL"),
            session_idle_minutes=int(os.environ.get("SESSION_IDLE_MINUTES", "1440")),
            data_dir=os.environ.get("KAIGARA_DATA_DIR", str(os.path.expanduser("~/.kaigara"))),
        )

        # Telegram
        telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if telegram_token:
            allowed = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
            config.platforms[Platform.TELEGRAM] = PlatformConfig(
                enabled=True,
                token=telegram_token,
                home_channel=os.environ.get("TELEGRAM_HOME_CHANNEL", ""),
                allowed_users=[u.strip() for u in allowed.split(",") if u.strip()],
            )

        # Slack
        slack_bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        if slack_bot_token:
            allowed = os.environ.get("SLACK_ALLOWED_USERS", "")
            config.platforms[Platform.SLACK] = PlatformConfig(
                enabled=True,
                token=slack_bot_token,
                allowed_users=[u.strip() for u in allowed.split(",") if u.strip()],
                extra={
                    "app_token": os.environ.get("SLACK_APP_TOKEN", ""),
                },
            )

        return config
