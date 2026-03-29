"""Audit logger — logs requests to tools container without sensitive data."""

import logging
import time

log = logging.getLogger("kaigara.audit")


def log_request(endpoint: str, tool_name: str | None = None, role: str = "unknown"):
    """Log a request to the audit trail. Never logs request/response bodies."""
    log.info(
        "request endpoint=%s tool=%s role=%s ts=%f",
        endpoint,
        tool_name or "-",
        role,
        time.time(),
    )
