"""Secret redaction helpers for logs and error messages.

The relay adapter must never emit API keys or ``Authorization`` headers.
These helpers make redaction explicit and testable.
"""

import re
from typing import Dict

# OpenAI-style secret keys (sk-...). Other providers use similar prefixes.
_API_KEY_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,})")
_AUTH_RE = re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)\S+")
# Generic key/secret/token assignments in logs (key="..." or token: abc123).
_GENERIC_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|token|secret|authorization|passwd|password)\s*[=:]\s*)(['\"]?)\S+\2"
)


def redact(text: str) -> str:
    """Return ``text`` with secrets replaced by ``***REDACTED***``."""
    if not text:
        return text
    text = _AUTH_RE.sub(r"\1***REDACTED***", text)
    text = _API_KEY_RE.sub("sk-***REDACTED***", text)
    text = _GENERIC_SECRET_RE.sub(r"\1***REDACTED***", text)
    return text


def redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Return a copy of ``headers`` with sensitive values redacted."""
    sensitive = {"authorization", "x-api-key", "api-key", "x-goog-api-key"}
    out: Dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in sensitive:
            out[key] = "***REDACTED***"
        else:
            out[key] = value
    return out
