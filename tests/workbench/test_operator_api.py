from __future__ import annotations

import contextlib
import http.client
import json
import threading

import pytest

from hermes_workbench.operator_api import create_server
from hermes_worker.constants import ALLOWED_GITHUB_REPOS
from hermes_worker.control_plane import ControlPlane


ALLOWED_REPO = sorted(ALLOWED_GITHUB_REPOS)[0]
MAX_BODY_BYTES = 65_536


@pytest.fixture
def cp(tmp_path):
    plane = ControlPlane(str(tmp_path / "operator-api.sqlite3"))
    try:
        yield plane
    finally:
        plane.conn.close()


@contextlib.contextmanager
def _running_server(cp):
    server = create_server(
        cp,
        host="127.0.0.1",
        port=0,
        allowed_repos={ALLOWED_REPO},
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _request(port, method, path, body=None, headers=None):
    request_headers = dict(headers or {})
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif isinstance(body, str):
        body = body.encode("utf-8")

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(
            method,
            path,
            body=body,
            headers=request_headers,
        )
        response = connection.getresponse()
        raw = response.read()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        payload = json.loads(raw.decode("utf-8")) if raw else None
        return response.status, payload, response_headers
    finally:
        connection.close()


def _session(port):
    status, payload, headers = _request(
        port,
        "GET",
        "/api/hermes/v1/session",
    )
    assert status == 200
    cookie_header = headers["set-cookie"]
    return payload, cookie_header.split(";", 1)[0], headers


def _operator_headers(port, cookie, csrf_token):
    return {
        "Cookie": cookie,
        "Origin": f"http://127.0.0.1:{port}",
        "X-CSRF-Token": csrf_token,
        "Content-Type": "application/json",
    }


def _create_task(port, request_id="req-1"):
    session, cookie, _ = _session(port)
    headers = _operator_headers(port, cookie, session["csrf_token"])
    status, payload, _ = _request(
        port,
        "POST",
        "/api/hermes/v1/tasks",
        {
            "request_id": request_id,
            "repo": ALLOWED_REPO,
            "goal": "实现低风险功能",
            "scope": "tests only",
            "acceptance": ["真实证据"],
        },
        headers,
    )
    return status, payload, headers, cookie


def test_session_bootstrap_sets_strict_local_owner_cookie_without_cors(cp):
    with _running_server(cp) as server:
        port = server.server_address[1]
        status, payload, headers = _request(
            port,
            "GET",
            "/api/hermes/v1/session",
        )

    assert status == 200
    assert payload["csrf_token"]
    assert payload["identity_mode"] == "loopback-local-owner"
    assert "HttpOnly" in headers["set-cookie"]
    assert "SameSite=Strict" in headers["set-cookie"]
    assert "access-control-allow-origin" not in headers


def test_task_post_rejects_missing_session_origin_csrf_bad_media_and_oversize_body(cp):
    task = {
        "request_id": "req-denied",
        "repo": ALLOWED_REPO,
        "goal": "实现低风险功能",
        "scope": "tests only",
        "acceptance": ["真实证据"],
    }
    with _running_server(cp) as server:
        port = server.server_address[1]
        session, cookie, _ = _session(port)
        csrf_token = session["csrf_token"]
        origin = f"http://127.0.0.1:{port}"

        status_no_session, _, _ = _request(
            port,
            "POST",
            "/api/hermes/v1/tasks",
            task,
            {"Origin": origin, "Content-Type": "application/json"},
        )
        status_no_origin, _, _ = _request(
            port,
            "POST",
            "/api/hermes/v1/tasks",
            task,
            {
                "Cookie": cookie,
                "X-CSRF-Token": csrf_token,
                "Content-Type": "application/json",
            },
        )
        status_no_csrf, _, _ = _request(
            port,
            "POST",
            "/api/hermes/v1/tasks",
            task,
            {
                "Cookie": cookie,
                "Origin": origin,
                "Content-Type": "application/json",
            },
        )
        status_bad_media, _, _ = _request(
            port,
            "POST",
            "/api/hermes/v1/tasks",
            json.dumps(task, ensure_ascii=False),
            {
                "Cookie": cookie,
                "Origin": origin,
                "X-CSRF-Token": csrf_token,
                "Content-Type": "text/plain",
            },
        )
        oversized = json.dumps(
            {**task, "request_id": "req-oversized", "goal": "x" * (MAX_BODY_BYTES + 1)}
        ).encode("utf-8")
        status_oversized, _, _ = _request(
            port,
            "POST",
            "/api/hermes/v1/tasks",
            oversized,
            _operator_headers(port, cookie, csrf_token),
        )

    assert status_no_session == 403
    assert status_no_origin == 403
    assert status_no_csrf == 403
    assert status_bad_media == 415
    assert status_oversized == 413
    assert cp.list_jobs() == []


def test_task_create_is_idempotent_and_persists_across_connections(cp):
    with _running_server(cp) as server:
        port = server.server_address[1]
        first_status, first_payload, headers, cookie = _create_task(port)
        second_status, second_payload, _ = _request(
            port,
            "POST",
            "/api/hermes/v1/tasks",
            {
                "request_id": "req-1",
                "repo": ALLOWED_REPO,
                "goal": "实现低风险功能",
                "scope": "tests only",
                "acceptance": ["真实证据"],
            },
            headers,
        )
        job_id = first_payload["task"]["job_id"]
        get_status, get_payload, _ = _request(
            port,
            "GET",
            f"/api/hermes/v1/tasks/{job_id}",
            headers={"Cookie": cookie},
        )

    assert first_status == 201
    assert second_status == 200
    assert first_payload["task"]["job_id"] == second_payload["task"]["job_id"]
    assert first_payload["task"]["task_id"] == second_payload["task"]["task_id"]
    assert first_payload["task"]["state"] == "pending"
    assert get_status == 200
    assert get_payload["task"]["job_id"] == job_id
    assert get_payload["task"]["task_id"] == first_payload["task"]["task_id"]
    assert get_payload["task"]["state"] == "pending"


def test_task_events_are_database_backed_and_provenanced(cp):
    with _running_server(cp) as server:
        port = server.server_address[1]
        create_status, create_payload, _, cookie = _create_task(port)
        job_id = create_payload["task"]["job_id"]
        status, payload, _ = _request(
            port,
            "GET",
            f"/api/hermes/v1/tasks/{job_id}/events?after=0",
            headers={"Cookie": cookie},
        )

    assert create_status == 201
    assert status == 200
    database_events = cp.get_events(job_id)
    assert len(payload["events"]) == len(database_events) == 1
    event = payload["events"][0]
    assert event["cursor"] == database_events[0]["id"]
    assert event["source_type"] == "operator"
    assert isinstance(event["source_id"], str) and event["source_id"]
    assert event["channel"] == "control-room"
    assert event["actor_role"] == "human_owner"
    assert all(
        item["actor_role"] not in {
            "hermes_master",
            "coding_agent",
            "reviewer",
            "qa_agent",
            "release_agent",
        }
        for item in payload["events"]
    )


def test_pause_resume_are_idempotent_and_merge_action_is_side_effect_free(cp):
    with _running_server(cp) as server:
        port = server.server_address[1]
        create_status, create_payload, headers, cookie = _create_task(port)
        job_id = create_payload["task"]["job_id"]
        action_path = f"/api/hermes/v1/tasks/{job_id}/actions"

        pause_statuses = [
            _request(
                port,
                "POST",
                action_path,
                {"request_id": "pause-1", "action": "pause"},
                headers,
            )[0]
            for _ in range(2)
        ]
        resume_statuses = [
            _request(
                port,
                "POST",
                action_path,
                {"request_id": "resume-1", "action": "resume"},
                headers,
            )[0]
            for _ in range(2)
        ]
        task_status, task_payload, _ = _request(
            port,
            "GET",
            f"/api/hermes/v1/tasks/{job_id}",
            headers={"Cookie": cookie},
        )
        events_status, events_payload, _ = _request(
            port,
            "GET",
            f"/api/hermes/v1/tasks/{job_id}/events?after=0",
            headers={"Cookie": cookie},
        )

        job_before_merge = cp.get_job(job_id)
        events_before_merge = cp.get_events(job_id)
        merge_status, _, _ = _request(
            port,
            "POST",
            action_path,
            {"request_id": "merge-1", "action": "merge"},
            headers,
        )
        job_after_merge = cp.get_job(job_id)
        events_after_merge = cp.get_events(job_id)

    assert create_status == 201
    assert all(status in {200, 202} for status in pause_statuses)
    assert all(status in {200, 202} for status in resume_statuses)
    assert task_status == 200
    assert task_payload["task"]["control_state"] == "active"
    assert events_status == 200
    event_types = [event["type"] for event in events_payload["events"]]
    assert event_types.count("operator.pause") == 1
    assert event_types.count("operator.resume") == 1
    assert merge_status in {400, 403}
    assert job_after_merge == job_before_merge
    assert events_after_merge == events_before_merge


def test_server_refuses_non_loopback_and_unknown_merge_route_is_404(cp):
    unexpected_server = None
    try:
        with pytest.raises(ValueError):
            unexpected_server = create_server(
                cp,
                host="0.0.0.0",
                port=0,
                allowed_repos={ALLOWED_REPO},
            )
    finally:
        if unexpected_server is not None:
            unexpected_server.server_close()

    with _running_server(cp) as server:
        port = server.server_address[1]
        status, _, _ = _request(port, "GET", "/merge")

    assert status == 404


def test_rejected_post_body_is_drained_without_tcp_reset(cp):
    task = {
        "request_id": "drain-probe",
        "repo": ALLOWED_REPO,
        "goal": "实现低风险功能",
        "scope": "tests only",
        "acceptance": ["真实证据"],
    }
    failures = []

    with _running_server(cp) as server:
        port = server.server_address[1]
        session, cookie, _ = _session(port)
        csrf_token = session["csrf_token"]
        origin = f"http://127.0.0.1:{port}"

        def no_session(iteration):
            return _request(
                port,
                "POST",
                "/api/hermes/v1/tasks",
                {**task, "request_id": f"no-session-{iteration}"},
                {"Origin": origin, "Content-Type": "application/json"},
            )

        def no_origin(iteration):
            return _request(
                port,
                "POST",
                "/api/hermes/v1/tasks",
                {**task, "request_id": f"no-origin-{iteration}"},
                {
                    "Cookie": cookie,
                    "X-CSRF-Token": csrf_token,
                    "Content-Type": "application/json",
                },
            )

        def no_csrf(iteration):
            return _request(
                port,
                "POST",
                "/api/hermes/v1/tasks",
                {**task, "request_id": f"no-csrf-{iteration}"},
                {
                    "Cookie": cookie,
                    "Origin": origin,
                    "Content-Type": "application/json",
                },
            )

        def bad_media(iteration):
            return _request(
                port,
                "POST",
                "/api/hermes/v1/tasks",
                json.dumps(
                    {**task, "request_id": f"bad-media-{iteration}"},
                    ensure_ascii=False,
                ),
                {
                    "Cookie": cookie,
                    "Origin": origin,
                    "X-CSRF-Token": csrf_token,
                    "Content-Type": "text/plain",
                },
            )

        def oversized(iteration):
            return _request(
                port,
                "POST",
                "/api/hermes/v1/tasks",
                json.dumps(
                    {
                        **task,
                        "request_id": f"oversized-{iteration}",
                        "goal": "x" * (MAX_BODY_BYTES + 1),
                    }
                ).encode("utf-8"),
                _operator_headers(port, cookie, csrf_token),
            )

        cases = (
            ("no_session", 403, no_session),
            ("no_origin", 403, no_origin),
            ("no_csrf", 403, no_csrf),
            ("bad_media", 415, bad_media),
            ("oversized", 413, oversized),
        )
        for name, expected_status, send in cases:
            for iteration in range(20):
                try:
                    status, _, _ = send(iteration)
                except ConnectionAbortedError as exc:
                    failures.append(
                        f"{name}[{iteration}] tcp_reset_winerror="
                        f"{getattr(exc, 'winerror', None)}"
                    )
                    continue
                if status != expected_status:
                    failures.append(
                        f"{name}[{iteration}] status={status}, "
                        f"expected={expected_status}"
                    )

    assert cp.list_jobs() == []
    assert failures == [], "\n".join(failures)
