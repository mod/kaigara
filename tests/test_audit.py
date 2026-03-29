"""Audit logging tests."""

import logging

from tools.audit import log_request


def test_audit_log_on_tool(caplog):
    with caplog.at_level(logging.INFO, logger="kaigara.audit"):
        log_request("/tool/web_search", tool_name="web_search", role="member")
    assert "web_search" in caplog.text
    assert "member" in caplog.text


def test_audit_no_secrets(caplog, monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "sk-secret-key-value")
    with caplog.at_level(logging.INFO, logger="kaigara.audit"):
        log_request("/tool/web_search", tool_name="web_search", role="owner")
    assert "sk-secret-key-value" not in caplog.text
