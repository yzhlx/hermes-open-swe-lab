"""Cloud Control Plane runnable for deployment verification (line C).

This module is a *deployment-time* wrapper. It does NOT implement the D3 core
state machine or the relay model adapter — those live in ``hermes_worker``
(Line A) and are used here as libraries only:

- the task queue / lease / event-store logic is ``hermes_worker.control_plane.ControlPlane``;
- the SQLite schema + WAL setup is ``hermes_worker.db``.

What this module ADDS (deployment concerns, not business logic):

- ``/healthz`` and ``/readyz`` endpoints (required by the MVP-0 deploy spec);
- hard security gates (no root, loopback-only bind, no secret logging);
- a clean ``main()`` entry point the systemd unit launches.

Line A may later replace this wrapper with its own server; the integration
contract in ``docs/deployment/DEPLOYMENT-INTEGRATION-CONTRACT.md`` pins the
env vars, entry point, and endpoint surface this deployment expects.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from hermes_worker.control_plane import ControlPlane, ControlPlaneError
from hermes_worker.db import init_db
from hermes_worker.worker_api_server import (
    resolve_production_worker_token_hashes, WorkerTokenConfigError,
    resolve_production_human_owner_token,
)

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def load_config() -> dict:
    """Read deploy-time config from the environment (set by the systemd
    EnvironmentFile). No secrets are read here — the worker token hash, if
    needed, is derived at request time from the ``X-Worker-Token`` header and
    never persisted or logged."""
    return {
        "db_path": _env("HERMES_DB_PATH", "runtime/events.db"),
        "host": _env("HERMES_LISTEN_HOST", "127.0.0.1"),
        "port": int(_env("HERMES_LISTEN_PORT", "8080")),
        "localhost_test": _env("HERMES_LOCALHOST_TEST", "0") == "1",
        "log_dir": _env("HERMES_LOG_DIR", "logs"),
    }


def guard_startup(cfg: dict) -> None:
    """Fail-closed startup guards. Any violation prints a clear message to
    stderr and exits non-zero so systemd marks the start as failed."""
    # Gate: never run the Control Plane as root.
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            print("SECURITY: refusing to start Control Plane as root "
                  "(use the dedicated 'hermes-swe' service user).", file=sys.stderr)
            sys.exit(2)
    except AttributeError:
        pass  # non-POSIX platforms (e.g. local Windows test) skip the check

    # Gate: never bind a non-loopback address → no public plaintext HTTP.
    if cfg["host"] not in LOOPBACK_HOSTS:
        print(f"SECURITY: refusing to bind non-loopback host '{cfg['host']}'. "
              f"The Control Plane must bind loopback only; terminate TLS at the "
              f"reverse proxy. Set HERMES_LISTEN_HOST=127.0.0.1.",
              file=sys.stderr)
        sys.exit(3)


def make_handler(db_path: str, allowed_token_hashes=None, mode: str = "dev",
                 human_owner_token=None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _cp(self) -> ControlPlane:
            # Fresh per-request connection: sqlite connections are thread-bound.
            # Production mode is fail-closed: an empty/None allowlist raises at
            # construction, so the server never admits any worker by default.
            return ControlPlane(db_path, allowed_token_hashes=allowed_token_hashes,
                                mode=mode, human_owner_token=human_owner_token)

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
            # The raw token is used only to compute a sha256 hash in-process.
            # It is NEVER logged, stored, or echoed.
            return self.headers.get("X-Worker-Token", "")

        def log_message(self, *args):
            pass  # silence default logging; no secrets must ever reach logs

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/healthz":
                self._send(200, {"status": "ok",
                                 "service": "hermes-swe-control-plane"})
                return
            if path == "/readyz":
                try:
                    conn = init_db(db_path)
                    conn.execute("SELECT 1 FROM jobs LIMIT 1")
                    conn.close()
                    self._send(200, {"ready": True})
                except Exception as e:  # noqa: BLE001
                    self._send(503, {"ready": False,
                                     "reason": type(e).__name__})
                return
            self._send(404, {"error": "not_found"})

        def do_POST(self):
            cp = self._cp()
            try:
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
                elif path.startswith("/worker/jobs/") and path.endswith("/complete"):
                    jid = int(path.split("/")[-2])
                    res = cp.complete(tok, jid, body.get("result"))
                elif path.startswith("/worker/jobs/") and path.endswith("/fail"):
                    jid = int(path.split("/")[-2])
                    res = cp.fail(tok, jid, body.get("error"))
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


def run_server(cfg: dict, allowed_token_hashes=None, mode: str = "dev",
               human_owner_token=None) -> ThreadingHTTPServer:
    """Build the server WITHOUT blocking (library-friendly)."""
    return ThreadingHTTPServer((cfg["host"], cfg["port"]),
                               make_handler(cfg["db_path"], allowed_token_hashes,
                                            mode, human_owner_token=human_owner_token))


def main():
    cfg = load_config()

    # Fail-closed startup: production requires an explicit, non-empty worker
    # allowlist from ALLOWED_WORKER_TOKENS. Missing / empty / malformed /
    # owner-token-collision -> the process refuses to start with a SECURITY
    # summary on stderr (no token is ever printed) and never falls back to dev
    # allow-all. Resolution happens BEFORE the socket binds.
    try:
        allowed_hashes = resolve_production_worker_token_hashes(
            os.environ.get("ALLOWED_WORKER_TOKENS"))
    except WorkerTokenConfigError as e:
        print(f"SECURITY: Control Plane startup aborted — {e}. "
              f"Set a non-empty ALLOWED_WORKER_TOKENS (comma-separated) and "
              f"restart. The service will NOT fall back to dev allow-all.",
              file=sys.stderr)
        sys.exit(2)

    # Fail-closed: the Human-Owner token must also be explicitly configured for
    # production. This runs BEFORE the socket binds / guard_startup so the
    # control plane never starts without the independent final-acceptance
    # secret (no port listen, no allow-all fallback).
    try:
        owner = resolve_production_human_owner_token(
            os.environ.get("HERMES_HUMAN_OWNER_TOKEN"))
    except WorkerTokenConfigError as e:
        print(f"SECURITY: Control Plane startup aborted — {e}. "
              f"Set HERMES_HUMAN_OWNER_TOKEN to a real secret and restart. "
              f"The service will NOT fall back to a placeholder default.",
              file=sys.stderr)
        sys.exit(2)

    guard_startup(cfg)

    # Ensure runtime dir exists with safe perms before opening the DB.
    parent = os.path.dirname(cfg["db_path"]) or "."
    os.makedirs(parent, exist_ok=True)
    try:
        os.chmod(parent, 0o750)
    except OSError:
        pass

    srv = run_server(cfg, allowed_token_hashes=allowed_hashes, mode="production",
                     human_owner_token=owner)
    stop = threading.Event()

    def _handle(signum, frame):  # noqa: ANN001
        stop.set()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    for s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(s, _handle)
        except (ValueError, AttributeError):
            pass  # not in main thread / unsupported platform

    scheme = "http" if cfg["localhost_test"] else "http(loopback,fronted-by-TLS)"
    print(f"Hermes Control Plane on {cfg['host']}:{cfg['port']} "
          f"(db={cfg['db_path']}, mode=production, {scheme})", flush=True)
    srv.serve_forever()
    print("Hermes Control Plane stopped.", flush=True)


if __name__ == "__main__":
    main()
