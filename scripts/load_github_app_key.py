#!/usr/bin/env python3
"""Minimal GitHub App private-key loader (deployment-side adapter).

WHY THIS EXISTS
---------------
Open SWE (langchain-ai/open-swe @ ed12bb8d...) reads the GitHub App private
key ONLY from the environment variable ``GITHUB_APP_PRIVATE_KEY`` as PEM *text*
(see ``agent/utils/github_app.py``: ``GITHUB_APP_PRIVATE_KEY =
os.environ.get("GITHUB_APP_PRIVATE_KEY", "")`` and ``private_key =
GITHUB_APP_PRIVATE_KEY.replace("\\n", "\n")``). It does NOT support a file
path.

To keep the secret on disk (in ``/etc/hermes-open-swe-lab/secrets/``) and OUT
of ``.env`` / git / logs, this loader reads the PEM from
``GITHUB_APP_PRIVATE_KEY_PATH`` and exports it into the live process
environment as ``GITHUB_APP_PRIVATE_KEY`` *before* Open SWE imports
``agent.utils.github_app``.

SAFETY
------
- The PEM text is placed ONLY in the process environment at runtime. It is
  never written to disk, ``.env``, or any log.
- A missing/unreadable key file fails loudly (RuntimeError) — it never falls
  back to an empty key.
- If ``GITHUB_APP_PRIVATE_KEY_PATH`` is unset, this does nothing, so the
  upstream default (reading ``GITHUB_APP_PRIVATE_KEY`` directly) is preserved.

USAGE
-----
Call ``load_github_app_private_key()`` from the control-plane entrypoint
BEFORE importing anything that pulls in ``agent.utils.github_app``. Example:

    from load_github_app_key import load_github_app_private_key
    load_github_app_private_key()   # populates GITHUB_APP_PRIVATE_KEY if path set
    # ... now import / launch Open SWE ...

Shell-only alternative (must run before launching Open SWE):

    export GITHUB_APP_PRIVATE_KEY="$(cat "$GITHUB_APP_PRIVATE_KEY_PATH")"
"""
import os


def load_github_app_private_key() -> None:
    """Populate GITHUB_APP_PRIVATE_KEY from GITHUB_APP_PRIVATE_KEY_PATH.

    No-op (preserves upstream default) when the path is unset. Raises on a
    configured-but-unreadable or non-PEM file.
    """
    path = (os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH") or "").strip()
    if not path:
        # Upstream default: GITHUB_APP_PRIVATE_KEY is read directly from env.
        return
    if os.environ.get("GITHUB_APP_PRIVATE_KEY"):
        # Already provided as text; respect it (defense in depth).
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            pem = fh.read()
    except OSError as exc:
        raise RuntimeError(
            f"GITHUB_APP_PRIVATE_KEY_PATH is set ({path!r}) but the key file "
            f"could not be read: {exc}"
        ) from exc
    if "PRIVATE KEY" not in pem:
        raise RuntimeError(
            f"File at GITHUB_APP_PRIVATE_KEY_PATH ({path!r}) does not look like "
            f"a PEM private key (missing 'PRIVATE KEY' marker)."
        )
    os.environ["GITHUB_APP_PRIVATE_KEY"] = pem


if __name__ == "__main__":
    load_github_app_private_key()
