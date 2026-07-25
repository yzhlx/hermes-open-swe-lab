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

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .control_plane import ControlPlane, ControlPlaneError


def make_handler(db_path: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _cp(self) -> ControlPlane:
            return ControlPlane(db_path)

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


def run_server(host="0.0.0.0", port=8080, db_path="runtime/events.db"):
    """Create and return the Worker API server WITHOUT blocking.

    The caller is responsible for starting the serve loop (e.g. in a daemon
    thread for tests, or ``serve_forever`` for the real control plane). This
    split keeps ``run_server`` usable as a library function and avoids the
    historical bug where it blocked forever and the test harness never returned.
    """
    return ThreadingHTTPServer((host, port), make_handler(db_path))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Hermes Worker API (control plane)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--db", default="runtime/events.db")
    args = ap.parse_args()
    srv = run_server(args.host, args.port, args.db)
    print(f"Hermes Worker API listening on {args.host}:{args.port} (db={args.db})",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
