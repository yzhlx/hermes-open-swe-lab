"""Pre-push diff secret scan for the controlled-delivery layer (D4).

This is a *small, single-purpose* module that reuses the existing
``hermes_worker.redact`` redactor. It does NOT implement its own secret
patterns — it asks ``redact.redact`` whether the diff text changed, and if so
returns only **redacted hit summaries** (never the raw secret).

Why this exists (closes the recon gap "no pre-push secret scan"): the delivery
layer must refuse to push a commit whose diff leaks a credential, and it must do
so *before* the push or PR. The raw secret must never appear in the returned
summary, the delivery log, the error text, or the PR body.
"""
from __future__ import annotations

import re

from .redact import redact as _redact_text

_REDACTED = "***REDACTED***"
# Max safe prefix captured before a redaction marker (no secret can survive
# redaction, so the prefix is always safe context like "github_pat_" / "token=").
_PREFIX_LOOKBEHIND = 24


def scan_diff_for_secrets(diff_text: str) -> list:
    """Return redacted hit summaries for any secret found in ``diff_text``.

    * Empty list  -> no secret detected (safe to continue).
    * Non-empty   -> at least one secret; each entry is the safe context that
      preceded a ``***REDACTED***`` marker. The original secret value is NEVER
      present in the returned list.

    The detection itself is delegated to ``redact.redact`` so the pattern set
    stays a single source of truth (no duplicated secret rules).
    """
    if not diff_text:
        return []
    cleaned = _redact_text(diff_text)
    if cleaned == diff_text:
        return []
    summaries: list = []
    seen = set()
    for m in re.finditer(re.escape(_REDACTED), cleaned):
        start = max(0, m.start() - _PREFIX_LOOKBEHIND)
        prefix = cleaned[start:m.start()]
        summary = f"{prefix}{_REDACTED}"
        if summary not in seen:
            seen.add(summary)
            summaries.append(summary)
    return summaries
