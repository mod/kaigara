"""Secret redaction — scans and sanitizes all output before returning to agent."""

import os
import re

REDACTED = "[REDACTED]"

# Known secret patterns
SECRET_PATTERNS = [
    re.compile(r"sk-or-v1-[a-zA-Z0-9]{40,}"),  # OpenRouter
    re.compile(r"sk-ant-[a-zA-Z0-9-]{20,}"),  # Anthropic
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),  # OpenAI
    re.compile(r"ghp_[a-zA-Z0-9]{36,}"),  # GitHub PAT
    re.compile(r"gho_[a-zA-Z0-9]{36,}"),  # GitHub OAuth
    re.compile(r"xox[bp]-[a-zA-Z0-9-]{20,}"),  # Slack
    re.compile(r"AKIA[A-Z0-9]{16}"),  # AWS access key
    re.compile(r"whsec_[a-zA-Z0-9]{20,}"),  # Webhook secret
    re.compile(r"Bearer\s+[a-zA-Z0-9._-]{20,}"),  # Bearer token
]

# Env var name patterns that contain secrets
SECRET_ENV_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")


class SecretRedactor:
    def __init__(self):
        self._env_values: set[str] = set()
        self._load_env_values()

    def _load_env_values(self):
        """Collect values of env vars that look like secrets."""
        for key, value in os.environ.items():
            if any(key.endswith(s) for s in SECRET_ENV_SUFFIXES):
                if value and len(value) > 4:  # skip trivially short values
                    self._env_values.add(value)

    def redact(self, text: str) -> tuple[str, bool]:
        """Redact secrets from text. Returns (redacted_text, was_redacted)."""
        original = text
        # Pattern-based redaction
        for pattern in SECRET_PATTERNS:
            text = pattern.sub(REDACTED, text)

        # Env value redaction
        for value in self._env_values:
            if value in text:
                text = text.replace(value, REDACTED)

        return text, text != original
