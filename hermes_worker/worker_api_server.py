"""Hardened Worker API surface shared by cloud deployment adapters."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .constants import (
    HOST_WORKER_ACTIVE_STATES,
    ROLE_CODING_AGENT,
)
from .control_plane import ControlPlane, ControlPlaneError
from .db import hash_token, init_db


MAX_BODY_BYTES = 1_048_576
_HOST_PRIVILEGED_EVENTS = {"pr_create_started"}
_HANDOFF_EVENTS = {"pr_created", "round2_push"}


def make_handler(
    db_path: str,
    allowed_tokens=None,
    allowed_token_hashes=None,
    host_worker_tokens=None,
    host_worker_token_hashes=None,
    replay_window: int = 300,
    lease_seconds: int = 1200,
    broker=None,
    health_endpoints: bool = False,
):
    allowed_hashes = set(allowed_token_hashes or ())
    allowed_hashes.update(hash_token(token) for token in (allowed_tokens or ()))
    allowed_hashes = allowed_hashes or None

    host_hashes = set(host_worker_token_hashes or ())
    host_hashes.update(hash_token(token) for token in (host_worker_tokens or ()))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _cp(self) -> ControlPlane:
            return ControlPlane(
                db_path,
                allowed_token_hashes=allowed_hashes,
                replay_window=replay_window,
                lease_seconds=lease_seconds,
            )

        def _send(self, code: int, value: dict) -> None:
            body = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ControlPlaneError("invalid_content_length")
            if length < 0 or length > MAX_BODY_BYTES:
                raise ControlPlaneError("payload_too_large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ControlPlaneError("invalid_json") from None
            if not isinstance(value, dict):
                raise ControlPlaneError("json_object_required")
            return value

        def _token(self) -> str:
            return self.headers.get("X-Worker-Token", "")

        def _require_host_worker(
            self,
            cp: ControlPlane,
            token: str,
            job_id: int,
            *,
            require_owner: bool = True,
        ) -> dict:
            token_hash = cp._check_token(token)
            if token_hash not in host_hashes:
                raise ControlPlaneError("host_worker_required")
            job = cp._get_job(job_id)
            if require_owner and job.get("worker_token_hash") != token_hash:
                raise ControlPlaneError("job_not_owned_by_worker")
            return job

        def _snapshot(self, cp: ControlPlane, token: str, job_id: int) -> dict:
            token_hash = cp._check_token(token)
            job = cp._get_job(job_id)
            owner = job.get("worker_token_hash")
            if owner is not None and owner != token_hash:
                raise ControlPlaneError("job_not_owned_by_worker")
            if owner is None and job.get("state") not in {"pending", "agent_done"}:
                raise ControlPlaneError("job_not_readable_by_worker")
            return {"job": job, "events": cp.get_events(job_id)}

        def _worker_identity(self, cp: ControlPlane, token: str) -> str:
            token_hash = cp._check_token(token)
            row = cp.conn.execute(
                "SELECT worker_id FROM workers WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
            if not row:
                raise ControlPlaneError("unknown_worker_token")
            return row["worker_id"]

        def log_message(self, *_args) -> None:
            return None

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if health_endpoints and path == "/healthz":
                self._send(200, {"status": "ok", "service": "hermes-control-plane"})
                return
            if health_endpoints and path == "/readyz":
                try:
                    conn = init_db(db_path)
                    conn.execute("SELECT 1 FROM jobs LIMIT 1")
                    conn.close()
                    self._send(200, {"ready": True})
                except Exception:
                    self._send(503, {"ready": False})
                return
            self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            cp = self._cp()
            sent = False
            try:
                cp.check_replay(
                    self.headers.get("X-Nonce"),
                    self.headers.get("X-Timestamp"),
                )
                path = urlparse(self.path).path
                body = self._body()
                token = self._token()

                if path == "/worker/register":
                    result = cp.register(
                        token,
                        body.get("name"),
                        json.dumps(body.get("capabilities")),
                    )
                elif path == "/worker/heartbeat":
                    result = cp.heartbeat(token, body.get("job_id"))
                elif path == "/worker/jobs/claim":
                    result = cp.claim(token)
                elif path.startswith("/worker/jobs/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4:
                        raise ControlPlaneError("route_not_found")
                    job_id = int(parts[2])
                    action = parts[3]
                    if action == "claim":
                        self._require_host_worker(
                            cp, token, job_id, require_owner=False
                        )
                        result = cp.claim_job(token, job_id)
                    elif action == "snapshot":
                        self._require_host_worker(
                            cp, token, job_id, require_owner=False
                        )
                        result = self._snapshot(cp, token, job_id)
                    elif action == "keepalive":
                        result = cp.keepalive(token, job_id)
                    elif action == "events":
                        result = cp.post_events(
                            token, job_id, body.get("events", [])
                        )
                    elif action == "host-events":
                        job = self._require_host_worker(cp, token, job_id)
                        worker_id = self._worker_identity(cp, token)
                        events = body.get("events", [])
                        if not isinstance(events, list) or any(
                            event.get("type") not in _HOST_PRIVILEGED_EVENTS
                            for event in events
                        ):
                            raise ControlPlaneError("host_event_type_forbidden")
                        for event in events:
                            cp.append_event(job_id, {
                                "id": event.get("id"),
                                "type": event.get("type"),
                                "payload": event.get("payload", {}),
                                "source_type": "worker",
                                "source_id": worker_id,
                                "actor_role": job.get("role") or ROLE_CODING_AGENT,
                            })
                        result = {"ok": True, "accepted": len(events)}
                    elif action == "transition":
                        self._require_host_worker(cp, token, job_id)
                        state = body.get("state")
                        if state not in HOST_WORKER_ACTIVE_STATES:
                            raise ControlPlaneError("invalid_host_worker_state")
                        cp.set_state(job_id, state)
                        cp.keepalive(token, job_id)
                        cp.post_events(token, job_id, [{
                            "type": state,
                            "payload": body.get("payload", {}),
                        }])
                        result = {"ok": True, "state": state}
                    elif action == "stage-result":
                        self._require_host_worker(cp, token, job_id)
                        cp.store_agent_result(job_id, body.get("result", {}))
                        result = {"ok": True}
                    elif action == "finish":
                        self._require_host_worker(cp, token, job_id)
                        result = cp.finish_state(
                            token,
                            job_id,
                            body.get("state"),
                            result=body.get("result", {}),
                            error=body.get("error"),
                        )
                    elif action == "complete":
                        result = cp.complete(token, job_id, body.get("result"))
                    elif action == "fail":
                        result = cp.fail(token, job_id, body.get("error"))
                    elif action == "handoff":
                        job = self._require_host_worker(cp, token, job_id)
                        event = body.get("event") or {}
                        if event.get("type") not in _HANDOFF_EVENTS:
                            raise ControlPlaneError("handoff_event_type_forbidden")
                        worker_id = self._worker_identity(cp, token)
                        cp.store_agent_result(job_id, body.get("result", {}))
                        cp.append_event(job_id, {
                            "id": event.get("id"),
                            "type": event.get("type"),
                            "payload": event.get("payload", {}),
                            "source_type": "worker",
                            "source_id": worker_id,
                            "actor_role": job.get("role") or ROLE_CODING_AGENT,
                        })
                        cp.set_state(job_id, "agent_done")
                        cp.update_job(job_id, lease_expires=None)
                        result = {"ok": True, "state": "agent_done"}
                    else:
                        raise ControlPlaneError("route_not_found")
                elif path.startswith("/internal/task/") and path.endswith("/token"):
                    parts = path.strip("/").split("/")
                    job_id = int(parts[2])
                    self._require_host_worker(cp, token, job_id)
                    if broker is None:
                        raise ControlPlaneError("token_broker_unavailable")
                    result = {
                        "token": broker.get_token_for_job(cp, job_id, token)
                    }
                else:
                    raise ControlPlaneError("route_not_found")

                self._send(200, result)
                sent = True
            except ControlPlaneError as exc:
                self._send(400, {"error": str(exc)})
                sent = True
            except (TypeError, ValueError):
                self._send(400, {"error": "invalid_request"})
                sent = True
            except Exception:
                self._send(500, {"error": "internal_error"})
                sent = True
            finally:
                cp.conn.close()
                if not sent:
                    self.close_connection = True

    return Handler


def run_server(
    host="0.0.0.0",
    port=8080,
    db_path="runtime/events.db",
    allowed_tokens=None,
    allowed_token_hashes=None,
    host_worker_tokens=None,
    host_worker_token_hashes=None,
    replay_window=300,
    lease_seconds=1200,
    broker=None,
    health_endpoints=False,
):
    return ThreadingHTTPServer(
        (host, port),
        make_handler(
            db_path,
            allowed_tokens=allowed_tokens,
            allowed_token_hashes=allowed_token_hashes,
            host_worker_tokens=host_worker_tokens,
            host_worker_token_hashes=host_worker_token_hashes,
            replay_window=replay_window,
            lease_seconds=lease_seconds,
            broker=broker,
            health_endpoints=health_endpoints,
        ),
    )


def main():
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Hermes Worker API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--db", default="runtime/events.db")
    parser.add_argument("--replay-window", type=int, default=300)
    args = parser.parse_args()
    allowed = [
        value.strip()
        for value in (os.environ.get("ALLOWED_WORKER_TOKENS") or "").split(",")
        if value.strip()
    ]
    server = run_server(
        args.host,
        args.port,
        args.db,
        allowed_tokens=allowed,
        replay_window=args.replay_window,
        health_endpoints=True,
    )
    print(
        f"Hermes Worker API listening on {args.host}:{args.port} (db={args.db})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
