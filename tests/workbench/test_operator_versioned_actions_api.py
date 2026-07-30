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
COMMIT_SHA = "b" * 40


@pytest.fixture
def cp(tmp_path):
    plane = ControlPlane(str(tmp_path / "operator-versioned-actions.sqlite3"))
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
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        payload = json.loads(raw.decode("utf-8")) if raw else None
        response_headers = {
            name.lower(): value for name, value in response.getheaders()
        }
        return response.status, payload, response_headers
    finally:
        connection.close()


def _operator_session(port):
    status, payload, headers = _request(
        port, "GET", "/api/hermes/v1/session"
    )
    assert status == 200
    cookie = headers["set-cookie"].split(";", 1)[0]
    write_headers = {
        "Cookie": cookie,
        "Origin": f"http://127.0.0.1:{port}",
        "X-CSRF-Token": payload["csrf_token"],
        "Content-Type": "application/json",
    }
    return cookie, write_headers


def _create_task(port, write_headers, request_id):
    return _request(
        port,
        "POST",
        "/api/hermes/v1/tasks",
        {
            "request_id": request_id,
            "repo": ALLOWED_REPO,
            "goal": "实现版本化人工控制",
            "scope": "workbench operator API",
            "acceptance": ["真实状态迁移", "无 Merge 副作用"],
        },
        write_headers,
    )


def _action(
    port,
    write_headers,
    job_id,
    *,
    action,
    request_id,
    expected_version,
    reason=None,
    requirements=None,
):
    return _request(
        port,
        "POST",
        f"/api/hermes/v1/tasks/{job_id}/actions",
        {
            "action": action,
            "request_id": request_id,
            "expected_version": expected_version,
            "reason": reason,
            "requirements": requirements,
        },
        write_headers,
    )


def test_create_and_get_task_expose_integer_version(cp):
    with _running_server(cp) as server:
        port = server.server_address[1]
        cookie, write_headers = _operator_session(port)
        create_status, create_payload, _ = _create_task(
            port, write_headers, "version-create-1"
        )
        job_id = create_payload["task"]["job_id"]
        get_status, get_payload, _ = _request(
            port,
            "GET",
            f"/api/hermes/v1/tasks/{job_id}",
            headers={"Cookie": cookie},
        )

    assert create_status == 201
    assert isinstance(create_payload["task"]["version"], int)
    assert get_status == 200
    assert isinstance(get_payload["task"]["version"], int)
    assert get_payload["task"]["version"] == create_payload["task"]["version"]


def test_supplement_forwards_versioned_fields_persists_requirements_and_replays(cp):
    requirements = [
        "继续使用当前 Draft PR",
        "补充空状态真实测试证据",
    ]
    reason = "Human Owner 补充验收要求。"
    with _running_server(cp) as server:
        port = server.server_address[1]
        cookie, write_headers = _operator_session(port)
        create_status, create_payload, _ = _create_task(
            port, write_headers, "supplement-create-1"
        )
        job_id = create_payload["task"]["job_id"]
        before = cp.get_job(job_id)
        before_version = int(before["version"])
        before_revision = int(before["requirements_revision"])

        supplement_status, supplement_payload, _ = _action(
            port,
            write_headers,
            job_id,
            action="supplement",
            request_id="supplement-http-1",
            expected_version=before_version,
            reason=reason,
            requirements=requirements,
        )
        replay_status, replay_payload, _ = _action(
            port,
            write_headers,
            job_id,
            action="supplement",
            request_id="supplement-http-1",
            expected_version=before_version,
            reason=reason,
            requirements=requirements,
        )
        events_status, events_payload, _ = _request(
            port,
            "GET",
            f"/api/hermes/v1/tasks/{job_id}/events?after=0",
            headers={"Cookie": cookie},
        )

    assert create_status == 201
    assert supplement_status in {200, 202}
    assert supplement_payload["task"]["version"] == before_version + 1
    assert (
        supplement_payload["task"]["requirements_revision"]
        == before_revision + 1
    )
    assert replay_status in {200, 202}
    assert replay_payload["result"] == supplement_payload["result"]
    assert replay_payload["task"]["version"] == supplement_payload["task"]["version"]
    assert events_status == 200
    supplement_events = [
        event
        for event in events_payload["events"]
        if event["raw_type"] == "operator_requirements_added"
    ]
    assert len(supplement_events) == 1
    assert supplement_events[0]["payload"]["requirements"] == requirements
    assert supplement_events[0]["payload"]["reason"] == reason


def test_stale_expected_version_returns_409_without_job_or_event_side_effect(cp):
    with _running_server(cp) as server:
        port = server.server_address[1]
        _, write_headers = _operator_session(port)
        _, create_payload, _ = _create_task(
            port, write_headers, "stale-create-1"
        )
        job_id = create_payload["task"]["job_id"]
        initial_version = int(cp.get_job(job_id)["version"])

        accepted_status, _, _ = _action(
            port,
            write_headers,
            job_id,
            action="supplement",
            request_id="stale-first",
            expected_version=initial_version,
            reason="First accepted request.",
            requirements=["First requirement."],
        )
        job_before_conflict = cp.get_job(job_id)
        events_before_conflict = cp.get_events(job_id)
        stale_status, stale_payload, _ = _action(
            port,
            write_headers,
            job_id,
            action="supplement",
            request_id="stale-second",
            expected_version=initial_version,
            reason="This request is stale.",
            requirements=["Must not be stored."],
        )

    assert accepted_status in {200, 202}
    assert stale_status == 409
    assert stale_payload["error"] == "version_conflict"
    assert cp.get_job(job_id) == job_before_conflict
    assert cp.get_events(job_id) == events_before_conflict


def test_approve_then_reject_preserves_same_pr_and_commit_without_completion(cp):
    with _running_server(cp) as server:
        port = server.server_address[1]
        _, write_headers = _operator_session(port)
        _, create_payload, _ = _create_task(
            port, write_headers, "approve-reject-create-1"
        )
        job_id = create_payload["task"]["job_id"]
        cp.update_job(
            job_id,
            state="await_user",
            pr_number=73,
            commit_sha=COMMIT_SHA,
            ci_status="success",
        )
        await_user = cp.get_job(job_id)

        approve_status, approve_payload, _ = _action(
            port,
            write_headers,
            job_id,
            action="approve",
            request_id="approve-http-1",
            expected_version=int(await_user["version"]),
            reason="Human acceptance granted; Merge remains manual.",
            requirements=None,
        )
        approved_task = approve_payload["task"]
        reject_status, reject_payload, _ = _action(
            port,
            write_headers,
            job_id,
            action="reject",
            request_id="reject-http-1",
            expected_version=approved_task["version"],
            reason="Return to the same PR for one more correction.",
            requirements=None,
        )
        rejected_task = reject_payload["task"]

    assert approve_status in {200, 202}
    assert approved_task["state"] == "ready_for_manual_merge"
    assert approved_task["state"] not in {"completed", "merged"}
    assert approved_task["pr_number"] == 73
    assert approved_task["commit_sha"] == COMMIT_SHA
    assert reject_status in {200, 202}
    assert rejected_task["state"] == "agent_done"
    assert rejected_task["pr_number"] == 73
    assert rejected_task["commit_sha"] == COMMIT_SHA
    assert rejected_task["state"] not in {"completed", "merged"}


def test_all_human_actions_are_reachable_and_forbidden_completion_is_atomic(cp):
    action_responses = []
    with _running_server(cp) as server:
        port = server.server_address[1]
        _, write_headers = _operator_session(port)
        _, create_payload, _ = _create_task(
            port, write_headers, "all-actions-create-1"
        )
        job_id = create_payload["task"]["job_id"]

        for action, request_id, reason, requirements in (
            ("pause", "all-pause", "Pause at a safe checkpoint.", None),
            ("resume", "all-resume", "Continue the same task.", None),
            (
                "supplement",
                "all-supplement",
                "Add one requirement.",
                ["Keep the existing task and evidence chain."],
            ),
        ):
            current_version = int(cp.get_job(job_id)["version"])
            status, payload, _ = _action(
                port,
                write_headers,
                job_id,
                action=action,
                request_id=request_id,
                expected_version=current_version,
                reason=reason,
                requirements=requirements,
            )
            action_responses.append((action, status, payload))

        cp.update_job(
            job_id,
            state="await_user",
            pr_number=91,
            commit_sha=COMMIT_SHA,
            ci_status="success",
        )
        for action, request_id, reason in (
            ("approve", "all-approve", "Accept for manual Merge."),
            ("reject", "all-reject", "Return to the same PR."),
        ):
            current_version = int(cp.get_job(job_id)["version"])
            status, payload, _ = _action(
                port,
                write_headers,
                job_id,
                action=action,
                request_id=request_id,
                expected_version=current_version,
                reason=reason,
                requirements=None,
            )
            action_responses.append((action, status, payload))

        job_before_forbidden = cp.get_job(job_id)
        events_before_forbidden = cp.get_events(job_id)
        forbidden_statuses = {}
        for action in ("merge", "auto_merge", "complete"):
            status, _, _ = _action(
                port,
                write_headers,
                job_id,
                action=action,
                request_id=f"forbidden-http-{action}",
                expected_version=int(job_before_forbidden["version"]),
                reason="Automation must never complete or Merge.",
                requirements=None,
            )
            forbidden_statuses[action] = status

    assert [item[0] for item in action_responses] == [
        "pause",
        "resume",
        "supplement",
        "approve",
        "reject",
    ]
    assert all(status in {200, 202} for _, status, _ in action_responses)
    assert all(
        isinstance(payload["task"]["version"], int)
        for _, _, payload in action_responses
    )
    assert all(status in {400, 403} for status in forbidden_statuses.values())
    assert cp.get_job(job_id) == job_before_forbidden
    assert cp.get_events(job_id) == events_before_forbidden
