"""Minimal Worker API HTTP server (stdlib only, no Flask).

Routes (all POST, JSON body, worker token in header ``X-Worker-Token``):

  POST /worker/register
  POST /worker/heartbeat
  POST /worker/jobs/claim
  POST /worker/jobs/{id}/events
  POST /worker/jobs/{id}/complete
  POST /worker/jobs/{id}/fail

The control plane runs on the cloud server. The local worker reaches it via
outbound HTTPS only. No secrets are logged; default request logging is silenced.

Threading note: SQLite connections are thread-bound, so we open a *fresh*
``ControlPlane`` (and thus a fresh connection) per request inside the handler
thread. WAL mode keeps the connections consistent for the control plane's
low throughput (MVP concurrency = 1). Never share a single sqlite connection
across threads.
"""
from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

from .control_plane import ControlPlane, ControlPlaneError
from .db import hash_token


class WorkerTokenConfigError(Exception):
    """Raised when production worker-token configuration is missing/invalid.

    This is a fail-closed signal only — it never carries the raw token value.
    """
    pass


def resolve_production_worker_tokens(env_value):
    """Validate ``ALLOWED_WORKER_TOKENS`` for production (fail-closed).

    Returns a non-empty list of usable token strings. Raises
    ``WorkerTokenConfigError`` when the value is unset, empty, yields no usable
    tokens (malformed / invalid format), or contains control characters.

    The caller hashes these tokens; they must never be logged or echoed.
    """
    if env_value is None:
        raise WorkerTokenConfigError(
            "ALLOWED_WORKER_TOKENS is not set; production requires an explicit "
            "worker allowlist (fail-closed)")
    tokens = [t.strip() for t in env_value.split(",") if t and t.strip()]
    if not tokens:
        raise WorkerTokenConfigError(
            "ALLOWED_WORKER_TOKENS is empty or contains no usable tokens "
            "(fail-closed)")
    for t in tokens:
        if any(ord(c) < 0x20 or ord(c) == 0x7f for c in t):
            raise WorkerTokenConfigError(
                "ALLOWED_WORKER_TOKENS contains invalid control characters "
                "(fail-closed)")
    return tokens


def resolve_production_worker_token_hashes(env_value):
    """Fail-closed resolve of ``ALLOWED_WORKER_TOKENS`` into a validated,
    non-empty ``set`` of sha256 token hashes for PRODUCTION use.

    This is the SINGLE source of truth for worker-token parsing — every
    production entry point (``worker_api_server``, ``event_router``,
    ``webhook_receiver``, ``control_plane_app``) MUST call this rather than
    re-implementing comma-split / whitespace-strip / control-char rejection.

    Structural guarantees (production config principles):
    * reuses :func:`resolve_production_worker_tokens` for parse + validation;
    * the Human-Owner token (``HERMES_HUMAN_OWNER_TOKEN``) is NEVER accepted as
      a worker token — listing it in ``ALLOWED_WORKER_TOKENS`` is rejected with
      ``WorkerTokenConfigError`` (the two trust chains stay independent);
    * the result is always a non-empty set (fail-closed otherwise);
    * raw tokens are hashed in-process and NEVER returned, logged, or echoed.
    """
    tokens = resolve_production_worker_tokens(env_value)
    # Guard: the Human Owner token must remain independent from worker tokens.
    owner = os.environ.get("HERMES_HUMAN_OWNER_TOKEN", "")
    _assert_owner_not_worker_token(tokens, owner)
    hashes = {hash_token(t) for t in tokens}
    if not hashes:
        raise WorkerTokenConfigError(
            "ALLOWED_WORKER_TOKENS yielded no usable token hashes "
            "(fail-closed)")
    return hashes


def resolve_production_human_owner_token(env_value):
    """Fail-closed resolve of ``HERMES_HUMAN_OWNER_TOKEN`` for PRODUCTION.

    Returns a non-empty, non-placeholder, control-character-free token string.
    Raises ``WorkerTokenConfigError`` when the value is unset, empty,
    whitespace-only, or still equals the removed placeholder default. The raw
    token is NEVER returned to logs or echoed. This is the SINGLE source of
    truth for the Human-Owner token — every production entry point MUST call
    this rather than reading the environment directly.

    The Human-Owner token is an INDEPENDENT secret from worker tokens; it must
    never be the same value as any ``ALLOWED_WORKER_TOKENS`` entry (enforced in
    ``ControlPlane.__init__`` via a hash comparison).
    """
    if env_value is None or not str(env_value).strip():
        raise WorkerTokenConfigError(
            "HERMES_HUMAN_OWNER_TOKEN is not set; production requires an "
            "explicit Human-Owner token (fail-closed)")
    token = str(env_value).strip()
    if token == "change-me-human-owner-token":
        raise WorkerTokenConfigError(
            "HERMES_HUMAN_OWNER_TOKEN still uses the placeholder default; "
            "set a real secret (fail-closed)")
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in token):
        raise WorkerTokenConfigError(
            "HERMES_HUMAN_OWNER_TOKEN contains invalid control characters "
            "(fail-closed)")
    return token


def _assert_owner_not_worker_token(worker_tokens, owner):
    """Fail-closed: the Human-Owner token must never equal any worker token.

    Shared by both ``resolve_production_worker_token_hashes`` (used by three
    entry points) and ``worker_api_server.main`` (which resolves raw worker
    tokens for ``run_server``). Keeping the check in one place guarantees every
    production entry point refuses a Human-Owner token that collides with a
    worker token.
    """
    if not owner:
        return
    for t in worker_tokens:
        if hmac.compare_digest(t, owner):
            raise WorkerTokenConfigError(
                "HERMES_HUMAN_OWNER_TOKEN must not be listed as an "
                "ALLOWED_WORKER_TOKENS value (fail-closed)")


def make_handler(db_path: str, allowed_tokens=None, replay_window: int = 300,
                lease_seconds: int = 1200, broker=None, mode: str = "dev",
                human_owner_token: Optional[str] = None):
    parsed = [t.strip() for t in (allowed_tokens or []) if isinstance(t, str) and t.strip()]
    allowed_hashes = {hash_token(t) for t in parsed} or None
    if mode == "production" and not allowed_hashes:
        # Fail-closed: refuse to build a handler that would allow any worker.
        raise WorkerTokenConfigError(
            "worker tokens not configured for production (fail-closed)")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _cp(self) -> ControlPlane:
            return ControlPlane(db_path, allowed_token_hashes=allowed_hashes,
                                replay_window=replay_window,
                                lease_seconds=lease_seconds, mode=mode,
                                human_owner_token=human_owner_token)

        def _send(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _token(self):
            return self.headers.get("X-Worker-Token", "")

        def log_message(self, *args):
            pass  # silence default logging (no secrets in logs)

        def do_POST(self):
            cp = self._cp()  # fresh per-request connection (thread-safe)
            try:
                # Replay protection (item 3): every request must carry a fresh,
                # unique nonce within the replay window.
                cp.check_replay(self.headers.get("X-Nonce"),
                                self.headers.get("X-Timestamp"))
                path = urlparse(self.path).path
                body = self._body()
                tok = self._token()
                if path == "/worker/register":
                    res = cp.register(tok, body.get("name"),
                                      json.dumps(body.get("capabilities")))
                elif path == "/worker/heartbeat":
                    res = cp.heartbeat(tok, body.get("job_id"))
                elif path == "/worker/jobs/claim":
                    res = cp.claim(tok)
                elif path.startswith("/worker/jobs/") and path.endswith("/events"):
                    jid = int(path.split("/")[-2])
                    res = cp.post_events(tok, jid, body.get("events", []))
                elif path.startswith("/worker/jobs/") and path.endswith("/keepalive"):
                    jid = int(path.split("/")[-2])
                    res = cp.keepalive(tok, jid)
                elif path.startswith("/worker/jobs/") and path.endswith("/complete"):
                    jid = int(path.split("/")[-2])
                    res = cp.complete(tok, jid, body.get("result"))
                elif path.startswith("/worker/jobs/") and path.endswith(
                        "/request-final-acceptance"):
                    # Open the gate (worker token; CI must already be green).
                    jid = int(path.split("/")[-2])
                    res = cp.request_final_acceptance(tok, jid)
                elif path.startswith("/worker/jobs/") and path.endswith(
                        "/final-accept"):
                    # Human-Owner acceptance. The Human Owner token travels on a
                    # DEDICATED header, never on X-Worker-Token, so a worker
                    # token can never impersonate the Human Owner (PB-23 req 6).
                    jid = int(path.split("/")[-2])
                    res = cp.final_accept(
                        self.headers.get("X-Human-Owner-Token", ""), jid)
                elif path.startswith("/worker/jobs/") and path.endswith("/fail"):
                    jid = int(path.split("/")[-2])
                    res = cp.fail(tok, jid, body.get("error"))
                elif path.startswith("/internal/task/") and path.endswith("/token"):
                    jid = int(path.split("/")[-2])
                    if broker is None:
                        self._send(501, {"error": "token_broker_unavailable"})
                        return
                    # Lease-gated token delivery (D3 requirement #7). The broker
                    # verifies the worker owns the job lease and the repo is
                    # allowlisted; the token is returned in-memory only.
                    try:
                        token = broker.get_token_for_job(cp, jid, tok)
                        self._send(200, {"token": token})
                    except ControlPlaneError as e:
                        self._send(400, {"error": str(e)})
                else:
                    self._send(404, {"error": "not_found"})
                    return
                self._send(200, res)
            except ControlPlaneError as e:
                self._send(400, {"error": str(e)})
            except Exception:  # noqa: BLE001 - surface as 500, never leak secrets
                self._send(500, {"error": "internal_error"})
            finally:
                try:
                    cp.conn.close()
                except Exception:
                    pass

    return Handler


def run_server(host="0.0.0.0", port=8080, db_path="runtime/events.db",
               allowed_tokens=None, replay_window=300, lease_seconds=1200,
               broker=None, mode: str = "dev",
               human_owner_token: Optional[str] = None):
    """Create and return the Worker API server WITHOUT blocking.

    The caller is responsible for starting the serve loop (e.g. in a daemon
    thread for tests, or ``serve_forever`` for the real control plane). This
    split keeps ``run_server`` usable as a library function and avoids the
    historical bug where it blocked forever and the test harness never returned.

    ``mode`` is ``"dev"`` by default (allow-all when no tokens are supplied, for
    offline tests). The production entry point ``main()`` always passes
    ``mode="production"``, which makes the server fail-closed when no worker
    tokens are configured. ``human_owner_token`` is injected into the control
    plane for the final-acceptance gate (fail-closed in production).
    """
    return ThreadingHTTPServer(
        (host, port),
        make_handler(db_path, allowed_tokens, replay_window, lease_seconds,
                     broker, mode, human_owner_token=human_owner_token))


def main():
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="Hermes Worker API (control plane)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--db", default="runtime/events.db")
    ap.add_argument("--replay-window", type=int, default=300)
    args = ap.parse_args()
    # Production MUST supply ALLOWED_WORKER_TOKENS (whitespace/comma separated).
    # Fail-closed: any misconfiguration aborts startup with a non-zero exit and
    # the server never starts in an allow-all state. No raw token is logged.
    try:
        allowed = resolve_production_worker_tokens(
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
    # Guard: the Human-Owner token must not collide with a worker token (the
    # two trust chains stay independent). Fail-closed before the socket binds.
    try:
        _assert_owner_not_worker_token(allowed, owner)
    except WorkerTokenConfigError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(2)
    srv = run_server(args.host, args.port, args.db,
                     allowed_tokens=allowed, replay_window=args.replay_window,
                     mode="production", human_owner_token=owner)
    print(f"Hermes Worker API listening on {args.host}:{args.port} (db={args.db})",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
