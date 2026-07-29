"""Loopback-only operator API for the Hermes engineering workbench.

This facade owns HTTP/session concerns only.  Durable task state, idempotency,
events, and projections remain in ``ControlPlane`` and its SQLite event store.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import socket
import threading
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from hermes_worker.control_plane import (
    ControlPlane,
    ControlPlaneError,
    trusted_event_channel,
    trusted_event_type,
)


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
DEFAULT_ALLOWED_REPOS = ("yzhlx/hermes-open-swe-smoke-test",)
MAX_JSON_BYTES = 65_536
MAX_DISCARD_BYTES = MAX_JSON_BYTES * 2
DISCARD_TIMEOUT_SECONDS = 0.25
SESSION_COOKIE = "hermes_operator_session"
CSRF_HEADER = "X-CSRF-Token"
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; base-uri 'none'; object-src 'none'; "
    "frame-ancestors 'none'"
)
STATIC_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/tokens.css": ("tokens.css", "text/css; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}


def _task_id(repo: str, request_id: str) -> str:
    digest = hashlib.sha256(
        (repo + "\0" + request_id).encode("utf-8")
    ).hexdigest()
    return "task_" + digest[:32]


def _json_payload(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _event_channel(event_type: str, payload: dict) -> str:
    return trusted_event_channel(event_type)


def _event_actor(source_id: str, payload: dict,
                 actor_role: str | None = None) -> str | None:
    return str(actor_role) if actor_role else None


def _display_payload(payload: dict) -> dict:
    reserved = {
        "source_type",
        "source_id",
        "actor_role",
        "role",
        "channel",
    }
    return {
        key: value for key, value in payload.items()
        if key not in reserved
    }


def _operator_event_type(raw_type: str) -> str:
    if raw_type in {"pause_requested", "paused"}:
        return "operator.pause"
    if raw_type == "resumed":
        return "operator.resume"
    return raw_type


def _clone_control_plane(control_plane: ControlPlane) -> ControlPlane:
    """Open a request-local SQLite connection with the supplied CP settings."""
    return ControlPlane(
        control_plane.db_path,
        now=control_plane._now,
        lease_seconds=control_plane.lease_seconds,
        allowed_token_hashes=control_plane.allowed_token_hashes,
        replay_window=control_plane.replay_window,
    )


def _task_view(cp: ControlPlane, job: dict) -> dict:
    payload = _json_payload(job.get("payload"))
    projection = cp.get_task_projection(
        job["id"], heartbeat_stale_after=30.0
    )
    return {
        "job_id": job["id"],
        "task_id": job.get("task_id"),
        "repo": job.get("repo"),
        "goal": payload.get("goal"),
        "request_id": payload.get("request_id"),
        "scope": payload.get("scope"),
        "acceptance": payload.get("acceptance"),
        "state": projection["state"],
        "control_state": projection["control_state"],
        "version": int(job.get("version") or 0),
        "requirements_revision": int(
            job.get("requirements_revision") or 0
        ),
        "connection_state": projection["connection_state"],
        "is_cached": projection["is_cached"],
        "last_heartbeat_at": projection["last_heartbeat_at"],
        "issue_number": job.get("issue_number"),
        "pr_number": job.get("pr_number"),
        "commit_sha": job.get("commit_sha"),
        "ci_status": job.get("ci_status"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "ended_at": job.get("ended_at"),
    }


def _event_view(event: dict) -> dict:
    payload = _display_payload(_json_payload(event.get("payload")))
    source_type = event.get("source_type") or "legacy"
    source_id = event.get("source_id") or "unverified"
    raw_type = event.get("event_type") or "unknown"
    actor_role = (
        None if source_type == "legacy" else event.get("actor_role")
    )
    display_trust = event.get("display_trust") or "unverified"
    return {
        "cursor": event["id"],
        "event_id": event.get("event_id"),
        "type": _operator_event_type(raw_type),
        "raw_type": raw_type,
        "timestamp": event.get("ts"),
        "source_type": source_type,
        "source_id": source_id,
        "channel": event.get("channel") or _event_channel(raw_type, payload),
        "actor_role": _event_actor(source_id, payload, actor_role),
        "unverified_display": (
            display_trust != "trusted"
            or source_type == "legacy"
            or not trusted_event_type(raw_type)
        ),
        "payload": payload,
    }


def _event_views(events: list[dict]) -> list[dict]:
    """Project raw events and collapse one atomic pause into one API event."""
    projected = []
    for event in events:
        view = _event_view(event)
        if (
            projected
            and projected[-1]["raw_type"] == "pause_requested"
            and view["raw_type"] == "paused"
            and projected[-1]["source_type"] == view["source_type"]
            and projected[-1]["source_id"] == view["source_id"]
            and projected[-1]["payload"].get("request_id")
            == view["payload"].get("request_id")
        ):
            projected[-1] = view
        else:
            projected.append(view)
    return projected


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def create_server(
    control_plane: ControlPlane,
    host: str = "127.0.0.1",
    port: int = 0,
    allowed_repos=DEFAULT_ALLOWED_REPOS,
    web_root=None,
) -> ThreadingHTTPServer:
    """Create, but do not start, the loopback-only operator server."""
    if host not in LOOPBACK_HOSTS:
        raise ValueError("operator_api_loopback_only")
    repos = frozenset(str(repo) for repo in allowed_repos)
    if not repos:
        raise ValueError("allowed_repos_required")

    sessions: dict[str, str] = {}
    sessions_lock = threading.Lock()
    runtime: dict[str, str] = {}
    static_root = (
        Path(web_root).resolve(strict=False)
        if web_root is not None else None
    )

    def static_candidate(path: str):
        asset = STATIC_ASSETS.get(path)
        if asset is None or static_root is None:
            return None
        candidate = (static_root / asset[0]).resolve(strict=False)
        if candidate.parent != static_root or not candidate.is_file():
            return None
        return candidate, asset[1]

    def ui_available() -> bool:
        return static_candidate("/index.html") is not None

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        def _send_json(self, status: int, value: dict,
                       headers: tuple[tuple[str, str], ...] = ()) -> None:
            body = json.dumps(
                value, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy", CONTENT_SECURITY_POLICY
            )
            if self.close_connection:
                self.send_header("Connection", "close")
            for name, content in headers:
                self.send_header(name, content)
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, candidate: Path, content_type: str) -> None:
            try:
                body = candidate.read_bytes()
            except OSError:
                self._not_found()
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy", CONTENT_SECURITY_POLICY
            )
            self.end_headers()
            self.wfile.write(body)

        def _not_found(self) -> None:
            self._send_json(404, {"error": "not_found"})

        def _send_json_and_close(self, status: int, value: dict) -> None:
            self.close_connection = True
            self._send_json(status, value)

        def _session(self) -> tuple[str, str] | None:
            raw_cookie = self.headers.get("Cookie", "")
            parsed = cookies.SimpleCookie()
            try:
                parsed.load(raw_cookie)
            except cookies.CookieError:
                return None
            morsel = parsed.get(SESSION_COOKIE)
            if morsel is None:
                return None
            session_id = morsel.value
            with sessions_lock:
                csrf = sessions.get(session_id)
            return (session_id, csrf) if csrf else None

        def _discard_request_body(self, length: int) -> None:
            remaining = min(length, MAX_DISCARD_BYTES)
            previous_timeout = self.connection.gettimeout()
            try:
                self.connection.settimeout(DISCARD_TIMEOUT_SECONDS)
                while remaining:
                    chunk = self.rfile.read(min(8_192, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except (OSError, TimeoutError):
                pass
            finally:
                try:
                    self.connection.settimeout(previous_timeout)
                except OSError:
                    pass

        def _read_framed_body(self) -> bytes | None:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._send_json_and_close(
                    411, {"error": "content_length_required"}
                )
                return None
            if length < 0:
                self._send_json_and_close(
                    400, {"error": "bad_content_length"}
                )
                return None
            if length > MAX_JSON_BYTES:
                self._discard_request_body(length)
                self._send_json_and_close(
                    413, {"error": "payload_too_large"}
                )
                return None
            raw = self.rfile.read(length)
            if len(raw) != length:
                self._send_json_and_close(400, {"error": "incomplete_body"})
                return None
            return raw

        def _read_write_json(self, raw: bytes) -> dict | None:
            if self.headers.get("Origin") != runtime["origin"]:
                self._send_json_and_close(
                    403, {"error": "origin_forbidden"}
                )
                return None
            session = self._session()
            supplied_csrf = self.headers.get(CSRF_HEADER, "")
            if session is None or not secrets.compare_digest(
                supplied_csrf, session[1]
            ):
                self._send_json_and_close(403, {"error": "csrf_forbidden"})
                return None
            content_type = self.headers.get("Content-Type", "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                self._send_json_and_close(415, {"error": "json_required"})
                return None
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._send_json_and_close(400, {"error": "invalid_json"})
                return None
            if not isinstance(value, dict):
                self._send_json_and_close(
                    400, {"error": "json_object_required"}
                )
                return None
            return value

        def _resolve_job(self, cp: ControlPlane, ref: str) -> dict:
            if ref.isdigit():
                return cp.get_job(int(ref))
            job = cp.get_job_by_task_id(ref)
            if job is None:
                raise ControlPlaneError("job_not_found")
            return job

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/healthz":
                self._send_json(200, {"ok": True, "status": "alive"})
                return
            if path == "/readyz":
                ready = ui_available()
                self._send_json(
                    200 if ready else 503,
                    {"ready": ready, "ui_available": ready},
                )
                return
            if path == "/api/hermes/v1/status":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "ui_available": ui_available(),
                        "identity_mode": "loopback-local-owner",
                    },
                )
                return
            if path in STATIC_ASSETS:
                static_file = static_candidate(path)
                if static_file is None:
                    self._not_found()
                else:
                    self._send_static(*static_file)
                return
            if path == "/api/hermes/v1/session":
                session_id = secrets.token_urlsafe(32)
                csrf = secrets.token_urlsafe(32)
                with sessions_lock:
                    sessions[session_id] = csrf
                cookie = (
                    f"{SESSION_COOKIE}={session_id}; Path=/; "
                    "HttpOnly; SameSite=Strict"
                )
                self._send_json(
                    200,
                    {
                        "csrf_token": csrf,
                        "identity_mode": "loopback-local-owner",
                    },
                    headers=(("Set-Cookie", cookie),),
                )
                return

            cp = _clone_control_plane(control_plane)
            try:
                if path == "/api/hermes/v1/tasks":
                    tasks = [
                        _task_view(cp, job) for job in cp.list_jobs()
                    ]
                    self._send_json(200, {"tasks": tasks})
                    return

                prefix = "/api/hermes/v1/tasks/"
                if path.startswith(prefix):
                    suffix = path[len(prefix):]
                    if suffix.endswith("/events"):
                        ref = suffix[:-len("/events")].rstrip("/")
                        job = self._resolve_job(cp, ref)
                        query = parse_qs(parsed.query, keep_blank_values=True)
                        try:
                            after_id = int(query.get("after", ["0"])[0])
                            limit = int(query.get("limit", ["100"])[0])
                        except ValueError:
                            self._send_json(400, {"error": "bad_cursor"})
                            return
                        if after_id < 0 or limit < 1 or limit > 500:
                            self._send_json(400, {"error": "bad_cursor"})
                            return
                        events = cp.get_events_after(
                            job["id"], after_id=after_id, limit=limit
                        )
                        projected = _event_views(events)
                        next_cursor = (
                            projected[-1]["cursor"] if projected else after_id
                        )
                        self._send_json(
                            200,
                            {
                                "task_id": job.get("task_id"),
                                "job_id": job["id"],
                                "after": after_id,
                                "next_cursor": next_cursor,
                                "events": projected,
                            },
                        )
                        return
                    if "/" not in suffix:
                        job = self._resolve_job(cp, suffix)
                        self._send_json(200, {"task": _task_view(cp, job)})
                        return
                self._not_found()
            except ControlPlaneError as exc:
                code = 404 if str(exc) == "job_not_found" else 400
                self._send_json(code, {"error": str(exc)})
            finally:
                cp.conn.close()

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            raw = self._read_framed_body()
            if raw is None:
                return
            lowered_segments = {part.lower() for part in path.split("/") if part}
            if "merge" in lowered_segments or "marge" in lowered_segments:
                self._send_json_and_close(404, {"error": "not_found"})
                return

            body = self._read_write_json(raw)
            if body is None:
                return

            cp = _clone_control_plane(control_plane)
            try:
                if path == "/api/hermes/v1/tasks":
                    repo = body.get("repo")
                    goal = body.get("goal")
                    request_id = body.get("request_id")
                    scope = body.get("scope")
                    acceptance = body.get("acceptance")
                    if not isinstance(repo, str) or repo not in repos:
                        self._send_json_and_close(
                            400, {"error": "repo_not_allowed"}
                        )
                        return
                    if not isinstance(goal, str) or not goal.strip():
                        self._send_json_and_close(
                            400, {"error": "goal_required"}
                        )
                        return
                    if not isinstance(request_id, str) or not request_id.strip():
                        self._send_json_and_close(
                            400, {"error": "request_id_required"}
                        )
                        return
                    goal = goal.strip()
                    request_id = request_id.strip()
                    task_id = _task_id(repo, request_id)
                    payload = {
                        "repo": repo,
                        "goal": goal,
                        "request_id": request_id,
                        "scope": scope,
                        "acceptance": acceptance,
                    }
                    job_id, created = cp.create_job_idempotent(
                        payload,
                        task_id=task_id,
                        repo=repo,
                        role="hermes_master",
                    )
                    cp.append_event(job_id, {
                        "id": f"operator:{task_id}:created",
                        "type": "task_created",
                        "source_type": "operator",
                        "source_id": "human_owner",
                        "payload": {
                            "repo": repo,
                            "goal": goal,
                            "request_id": request_id,
                            "scope": scope,
                            "acceptance": acceptance,
                            "channel": "control-room",
                            "actor_role": "human_owner",
                        },
                    })
                    job = cp.get_job(job_id)
                    self._send_json(
                        201 if created else 200,
                        {
                            "created": created,
                            "task": _task_view(cp, job),
                        },
                    )
                    return

                prefix = "/api/hermes/v1/tasks/"
                if path.startswith(prefix) and path.endswith("/actions"):
                    ref = path[len(prefix):-len("/actions")].rstrip("/")
                    if not ref or "/" in ref:
                        self._send_json_and_close(
                            404, {"error": "not_found"}
                        )
                        return
                    job = self._resolve_job(cp, ref)
                    action = body.get("action")
                    request_id = body.get("request_id")
                    expected_version = body.get("expected_version")
                    reason = body.get("reason")
                    requirements = body.get("requirements")
                    if not isinstance(request_id, str) or not request_id.strip():
                        self._send_json_and_close(
                            400, {"error": "request_id_required"}
                        )
                        return
                    if action not in {
                        "pause",
                        "resume",
                        "supplement",
                        "reject",
                        "approve",
                    }:
                        self._send_json_and_close(
                            400, {"error": "operator_action_not_allowed"}
                        )
                        return
                    result = cp.apply_operator_action(
                        job["id"],
                        action,
                        request_id=request_id.strip(),
                        expected_version=expected_version,
                        reason=reason,
                        requirements=requirements,
                    )
                    refreshed = cp.get_job(job["id"])
                    self._send_json(
                        200,
                        {
                            "result": result,
                            "task": _task_view(cp, refreshed),
                        },
                    )
                    return
                self._send_json_and_close(404, {"error": "not_found"})
            except ControlPlaneError as exc:
                if str(exc) == "job_not_found":
                    code = 404
                elif str(exc) == "version_conflict":
                    code = 409
                else:
                    code = 400
                self._send_json_and_close(code, {"error": str(exc)})
            finally:
                cp.conn.close()

    server_type = (
        _IPv6ThreadingHTTPServer if host == "::1" else ThreadingHTTPServer
    )
    server = server_type((host, port), Handler)
    actual_port = server.server_address[1]
    origin_host = f"[{host}]" if host == "::1" else host
    runtime["origin"] = f"http://{origin_host}:{actual_port}"
    server.daemon_threads = True
    return server
