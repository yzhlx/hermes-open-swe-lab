"""GitHub Webhook receiver (D3 hardening, item 3) — offline-validated.

Validates the GitHub ``X-Hub-Signature-256`` HMAC, deduplicates by delivery
id, scopes to the smoke-test repo only, and creates a control-plane job.

Deployment hard requirements (enforced in code):
- Signature must verify (constant-time HMAC-SHA256).
- Delivery id must be unique (replay/dedup store).
- Repo must be in ``ALLOWED_REPOS`` (only ``yzhlx/hermes-open-swe-smoke-test``).
- If ``require_tls`` is set, requests must arrive over https
  (``X-Forwarded-Proto: https``) — the public endpoint must sit behind TLS.

This module is exercised OFFLINE with a FAKE test secret. Production wires
``GITHUB_WEBHOOK_SECRET`` from the environment. **No real webhook secret is
generated or transmitted here, and the public webhook is NOT enabled by this
code path** (that requires the operator to expose the endpoint + set the real
secret). No smoke-test repo is modified; the relay model is never called.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .control_plane import ControlPlane, ControlPlaneError
from .worker_api_server import (
    resolve_production_worker_token_hashes, WorkerTokenConfigError,
    resolve_production_human_owner_token,
)

# Only the smoke-test repo may ever create a job through the webhook.
ALLOWED_REPOS = {"yzhlx/hermes-open-swe-smoke-test"}

# Placeholder ONLY. Tests pass this in; production must supply the real secret
# via GITHUB_WEBHOOK_SECRET (env). Never treat this as a real secret.
TEST_WEBHOOK_SECRET = "test-only-webhook-secret-DO-NOT-USE-IN-PRODUCTION"


def verify_signature(secret: str, raw_body: bytes, signature: str) -> bool:
    """Constant-time HMAC-SHA256 verification of an ``X-Hub-Signature-256``."""
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body or b"",
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature[len("sha256="):])


class WebhookReceiver:
    """Stateless config holder; spins a fresh ControlPlane per request.

    A fresh ``ControlPlane`` per call keeps SQLite connections thread-local
    (the HTTP server is threaded). Config (db path, secret, allowlist) is
    immutable and shared safely.
    """

    def __init__(self, db_path: str, secret: str,
                 allowed_repos: Optional[set] = None,
                 require_tls: bool = False,
                 allowed_token_hashes=None, replay_window: int = 300,
                 mode: str = "dev", human_owner_token: Optional[str] = None):
        self.db_path = db_path
        self.secret = secret
        self.allowed_repos = allowed_repos or ALLOWED_REPOS
        self.require_tls = require_tls
        self.allowed_token_hashes = allowed_token_hashes
        self.replay_window = replay_window
        # Fail-closed: a production WebhookReceiver MUST carry an explicit,
        # non-empty worker allowlist. Missing/empty -> construction refuses, so
        # no ControlPlane is ever created and no webhook is processed in an
        # allow-all state. dev/test modes are opt-in only.
        if mode == "production" and not allowed_token_hashes:
            raise WorkerTokenConfigError(
                "production WebhookReceiver requires a non-empty "
                "allowed_token_hashes (fail-closed)")
        self.mode = mode
        # Threaded through to ControlPlane for the final-acceptance gate. In
        # production the control plane construction refuses unless main()
        # injected a validated token.
        self.human_owner_token = human_owner_token

    def handle(self, *, delivery_id: str, signature: str, event: str,
               raw_body: bytes, repo: Optional[str] = None,
               forwarded_proto: Optional[str] = None) -> dict:
        """Validate + dedup + allowlist, then create a job.

        Returns a dict. Raises ``ControlPlaneError`` for any rejection:
        ``tls_required``, ``bad_signature``, ``repo_not_allowed``. A duplicate
        delivery id returns ``{"deduped": True, ...}`` (no new job).
        """
        if self.require_tls and forwarded_proto != "https":
            raise ControlPlaneError("tls_required")
        if not verify_signature(self.secret, raw_body, signature):
            raise ControlPlaneError("bad_signature")

        cp = ControlPlane(self.db_path,
                          allowed_token_hashes=self.allowed_token_hashes,
                          replay_window=self.replay_window,
                          mode=self.mode,
                          human_owner_token=self.human_owner_token)
        try:
            # Delivery-id dedup (GitHub guarantees uniqueness per delivery).
            try:
                cp.conn.execute(
                    "INSERT INTO deliveries(delivery_id, ts) VALUES (?,?)",
                    (delivery_id, cp._now()))
                cp.conn.commit()
            except sqlite3.IntegrityError:
                return {"deduped": True, "delivery_id": delivery_id}

            body = json.loads(raw_body or b"{}")
            repo = repo or body.get("repository", {}).get("full_name", "")
            if repo not in self.allowed_repos:
                raise ControlPlaneError("repo_not_allowed")

            jid = cp.create_job(
                {"event": event, "repo": repo, "payload": body},
                issue_number=body.get("issue", {}).get("number"),
                pr_number=body.get("pull_request", {}).get("number"),
                role="coding_agent")
            return {"ok": True, "job_id": jid, "delivery_id": delivery_id}
        finally:
            try:
                cp.conn.close()
            except Exception:
                pass

    # -- HTTP layer ---------------------------------------------------------
    def make_handler(self):
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass  # no secrets in default logs

            def _send(self, code, obj):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(n) if n else b"{}"
                sig = self.headers.get("X-Hub-Signature-256", "")
                delivery = self.headers.get("X-GitHub-Delivery", "")
                event = self.headers.get("X-GitHub-Event", "")
                proto = self.headers.get("X-Forwarded-Proto", "http")
                try:
                    res = receiver.handle(delivery_id=delivery, signature=sig,
                                          event=event, raw_body=raw,
                                          forwarded_proto=proto)
                    self._send(200, res)
                except ControlPlaneError as e:
                    err = str(e)
                    code = 403 if err in ("repo_not_allowed", "tls_required") \
                        else 401
                    self._send(code, {"error": err})

        return Handler

    def run_server(self, host="0.0.0.0", port=8081):
        """Return (do NOT start) the threaded webhook server."""
        return ThreadingHTTPServer((host, port), self.make_handler())


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Hermes GitHub Webhook Receiver (production)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--db", default="runtime/events.db")
    ap.add_argument("--secret-env", default="GITHUB_WEBHOOK_SECRET")
    args = ap.parse_args()

    # Fail-closed startup: production requires an explicit worker allowlist.
    # Missing / empty / malformed / owner-token-collision -> exit before the
    # socket binds (no ControlPlane created, no webhook processed). The error
    # carries no token value.
    try:
        hashes = resolve_production_worker_token_hashes(
            os.environ.get("ALLOWED_WORKER_TOKENS"))
    except WorkerTokenConfigError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        owner = resolve_production_human_owner_token(
            os.environ.get("HERMES_HUMAN_OWNER_TOKEN"))
    except WorkerTokenConfigError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(2)

    secret = os.environ.get(args.secret_env, "")
    recv = WebhookReceiver(args.db, secret, allowed_token_hashes=hashes,
                           mode="production", human_owner_token=owner)
    srv = recv.run_server(args.host, args.port)
    print(f"Hermes Webhook Receiver on {args.host}:{args.port} "
          f"(db={args.db}, mode=production)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
