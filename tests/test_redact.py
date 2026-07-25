"""Tests for secret redaction helpers."""

from hermes_open_swe_relay.redact import redact, redact_headers


def test_redact_authorization_header():
    msg = "request failed Authorization: Bearer sk-live-secret-token-123"
    out = redact(msg)
    assert "sk-live-secret-token-123" not in out
    assert "***REDACTED***" in out


def test_redact_openai_key():
    msg = "key=sk-REDACT-TEST-VALUE-0000"
    out = redact(msg)
    assert "sk-REDACT-TEST-VALUE-0000" not in out
    assert "sk-***REDACTED***" in out


def test_redact_generic_secret_assignment():
    msg = 'calling provider with api_key="super-secret-value"'
    out = redact(msg)
    assert "super-secret-value" not in out
    assert "***REDACTED***" in out


def test_redact_empty():
    assert redact("") == ""


def test_redact_headers_dict():
    headers = {
        "Authorization": "Bearer sk-secret",
        "X-API-Key": "abc123",
        "Content-Type": "application/json",
    }
    out = redact_headers(headers)
    assert out["Authorization"] == "***REDACTED***"
    assert out["X-API-Key"] == "***REDACTED***"
    assert out["Content-Type"] == "application/json"
