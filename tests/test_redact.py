"""Tests for secret redaction used by the Hermes Worker (D3 hardening, item 5).

The worker redacts docker command arguments, webhook payloads, and event/result
logs. It must cover secrets embedded in *commands* and *URLs*, not just
``-e KEY=VALUE`` env form — e.g. ``git push https://TOKEN@github.com/...`` or a
leaked GitHub PAT in stdout.
"""
from hermes_worker.redact import redact, redact_headers, redact_secret_env_value


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


def test_redact_github_pat_in_command():
    # Token pasted directly into a command (the dangerous case item 5 targets).
    cmd = "gh auth login --with-token ghp_FAKESECRETPAT0123456789ABCDEFGHIJKL"
    out = redact(cmd)
    assert "ghp_FAKESECRETPAT0123456789ABCDEFGHIJKL" not in out
    assert "ghp_***REDACTED***" in out


def test_redact_github_app_pat_in_command():
    cmd = "git push https://x-access-token:github_pat_FAKE_xxx_yyy@github.com/o/r.git"
    out = redact(cmd)
    assert "github_pat_FAKE_xxx_yyy" not in out


def test_redact_url_embedded_token():
    url = "git push https://ghp_FAKESECRETPAT0123456789ABCDEFGHIJKL@github.com/o/r.git main"
    out = redact(url)
    assert "ghp_FAKESECRETPAT0123456789ABCDEFGHIJKL" not in out
    assert "***REDACTED***" in out


def test_redact_url_user_password():
    url = "https://alice:s3cr3tpass@api.example.com/x"
    out = redact(url)
    assert "alice" not in out or "s3cr3tpass" not in out
    assert "***REDACTED***" in out


def test_redact_empty():
    assert redact("") == ""


def test_redact_headers_dict():
    headers = {
        "Authorization": "Bearer sk-secret",
        "X-API-Key": "abc123",
        "X-Hub-Signature-256": "sha256=deadbeef",
        "Content-Type": "application/json",
    }
    out = redact_headers(headers)
    assert out["Authorization"] == "***REDACTED***"
    assert out["X-API-Key"] == "***REDACTED***"
    assert out["X-Hub-Signature-256"] == "***REDACTED***"
    assert out["Content-Type"] == "application/json"


def test_redact_secret_env_value():
    assert redact_secret_env_value("GITHUB_TOKEN", "ghp_abc") == "***REDACTED***"
    assert redact_secret_env_value("PATH", "ghp_should_not_leak") == "***REDACTED***"
