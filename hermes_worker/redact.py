"""Secret redaction for the Hermes Worker (D3 hardening, item 5).

Used for: docker command arguments, webhook payloads, and event/result logs.
The existing relay redactor (``hermes_open_swe_relay.redact``) covers
``Authorization`` headers and ``sk-`` keys, but it does NOT cover GitHub
tokens or credentials embedded inside commands / URLs — which is exactly how
a coding agent would leak a token (``git push https://TOKEN@github.com/...``,
``gh auth login --with-token <TOKEN>``). This module closes that gap and is
the redactor the worker + docker backend actually use.

IMPORTANT: this module never stores secrets — it only rewrites strings for
logging / inspection. Real tokens live only in the server ``.env`` or as
short-lived credentials and are never written to SQLite, JSONL, or images.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

# GitHub Personal Access Tokens: ghp_ / gho_ / ghu_ / ghs_ / ghr_ + body.
# We redact the whole ghp_-prefixed run (>=8 chars, allows underscores) rather
# than requiring exactly 36 chars, so shortened/fuzzed PAT-shaped strings in a
# command or env value are still scrubbed (item 5: never leak a token via a
# command or URL).
_GITHUB_PAT_RE = re.compile(r"\b(gh[opushr]_)[A-Za-z0-9_]{8,}")
# GitHub App installation tokens: github_pat_ + 59 chars (with underscores).
_GITHUB_APP_PAT_RE = re.compile(r"\bgithub_pat_[A-Za-z0-9_]{59}\b")
# Authorization: Bearer <token>
_AUTH_BEARER_RE = re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)\S+")
# OpenAI-style keys.
_API_KEY_RE = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})\b")
# Generic key/secret/token/password assignments: key="..." / token: abc
_GENERIC_RE = re.compile(
    r"(?i)((?:api[_-]?key|token|secret|authorization|passwd|password|"
    r"client_secret|x-access-token)\s*[=:]\s*)(['\"]?)[^\s'\"]+\2"
)
# URL-embedded credentials: https://user:pass@host  (most specific)
_URL_CRED_RE = re.compile(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@")
# URL-embedded token: https://TOKEN@github.com
_URL_TOKEN_RE = re.compile(r"(?i)(https?://)[A-Za-z0-9_\-]+@")


def redact(text: str) -> str:
    """Return ``text`` with any embedded secret replaced by ``***REDACTED***``.

    Order matters: URL-embedded credentials are scrubbed first (most specific),
    then GitHub/provider tokens, then generic assignments.
    """
    if not text:
        return text
    # 1) URL-embedded credentials (https://user:pass@host)
    text = _URL_CRED_RE.sub(r"\1***REDACTED***:***REDACTED***@", text)
    # 2) URL-embedded token (https://TOKEN@github.com)
    text = _URL_TOKEN_RE.sub(r"\1***REDACTED***@", text)
    # 3) GitHub App installation token (longest, most specific GitHub form)
    text = _GITHUB_APP_PAT_RE.sub("github_pat_***REDACTED***", text)
    # 4) GitHub PAT prefixes (keep the ghp_ prefix, scrub the body)
    text = _GITHUB_PAT_RE.sub(lambda m: m.group(1) + "***REDACTED***", text)
    # 5) Authorization: Bearer <token>
    text = _AUTH_BEARER_RE.sub(r"\1***REDACTED***", text)
    # 6) OpenAI-style keys
    text = _API_KEY_RE.sub("sk-***REDACTED***", text)
    # 7) Generic assignments (api_key="...", token: abc, x-access-token=...)
    text = _GENERIC_RE.sub(r"\1***REDACTED***", text)
    return text


def redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Return a copy of ``headers`` with sensitive values redacted."""
    sensitive = {
        "authorization", "x-api-key", "api-key", "x-goog-api-key",
        "x-hub-signature-256", "x-hub-signature", "x-github-token",
    }
    out: Dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in sensitive:
            out[key] = "***REDACTED***"
        else:
            out[key] = value
    return out


def redact_secret_env_value(key: str, value: str) -> str:
    """Redact a single env value if its key is a known secret env var OR the
    value itself contains a token-shaped secret (e.g. a ``ghp_`` PAT assigned
    to an unrelated var like PATH). A leaked token must never reach logs/JSONL
    regardless of which env var it was placed in.
    """
    if key in {"GITHUB_TOKEN", "GITHUB_APP_TOKEN", "GH_TOKEN",
               "GITHUB_WEBHOOK_SECRET", "X-GITHUB-TOKEN"}:
        return "***REDACTED***"
    if redact(value) != value:
        return "***REDACTED***"
    return value
