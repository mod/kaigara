"""Secret redaction tests."""

from tools.redactor import SecretRedactor


def test_redact_openrouter_key():
    r = SecretRedactor()
    text = "key is sk-or-v1-abcdefghijklmnopqrstuvwxyz1234567890abcd"
    result, redacted = r.redact(text)
    assert "sk-or-v1-" not in result
    assert "[REDACTED]" in result
    assert redacted


def test_redact_github_token():
    r = SecretRedactor()
    text = "token: ghp_abcdefghijklmnopqrstuvwxyz1234567890"
    result, redacted = r.redact(text)
    assert "ghp_" not in result
    assert redacted


def test_redact_bearer():
    r = SecretRedactor()
    text = "Authorization: Bearer sk-abcdef1234567890abcdef"
    result, redacted = r.redact(text)
    assert "sk-abcdef" not in result
    assert redacted


def test_redact_env_values(monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "super-secret-value-12345")
    r = SecretRedactor()
    text = "the key is super-secret-value-12345 right here"
    result, redacted = r.redact(text)
    assert "super-secret-value-12345" not in result
    assert redacted


def test_no_false_positive():
    r = SecretRedactor()
    text = "The sky is blue and the grass is green."
    result, redacted = r.redact(text)
    assert result == text
    assert not redacted


def test_redact_in_error():
    r = SecretRedactor()
    error = "ConnectionError: failed to connect with key sk-abcdef1234567890abcdef"
    result, redacted = r.redact(error)
    assert "sk-abcdef" not in result
    assert redacted


def test_redact_aws_key():
    r = SecretRedactor()
    text = "access key: AKIAIOSFODNN7EXAMPLE"
    result, redacted = r.redact(text)
    assert "AKIA" not in result
    assert redacted


def test_redact_slack_token():
    r = SecretRedactor()
    text = "slack: xoxb-FAKE-TOKEN-FOR-TESTING"
    result, redacted = r.redact(text)
    assert "xoxb-" not in result
    assert redacted
