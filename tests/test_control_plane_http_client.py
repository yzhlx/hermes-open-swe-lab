from __future__ import annotations

import json
import threading
import time

import pytest

from hermes_worker.codex_job_runner import CodexJobRunner
from hermes_worker.control_plane import ControlPlane, ControlPlaneError
from hermes_worker.control_plane_http_client import ControlPlaneHttpClient
from hermes_worker.remote_token_broker import RemoteTokenBroker
from hermes_worker.worker_api_server import run_server


WORKER_TOKEN = "synthetic-phase2-worker-token"
OTHER_TOKEN = "synthetic-other-worker-token"


class _RecordingBroker:
    def __init__(self):
        self.calls = []

    def get_token_for_job(self, cp, job_id, worker_token):
        cp.keepalive(worker_token, job_id)
        self.calls.append(job_id)
        return "ghs_FAKE_PHASE2_INSTALLATION_TOKEN"


def _start_server(tmp_path, *, broker=None):
    db = tmp_path / "events.db"
    seed = ControlPlane(str(db))
    server = run_server(
        "127.0.0.1",
        0,
        str(db),
        allowed_tokens=[WORKER_TOKEN, OTHER_TOKEN],
        host_worker_tokens=[WORKER_TOKEN],
        broker=broker,
        health_endpoints=True,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return seed, server, thread, f"http://{host}:{port}"


def _stop(seed, server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    seed.conn.close()
    assert not thread.is_alive()


def _client(base_url, token=WORKER_TOKEN, **kwargs):
    return ControlPlaneHttpClient(
        base_url,
        token,
        insecure_local_ok=True,
        **kwargs,
    )


def test_remote_client_claim_transition_handoff_and_snapshot(tmp_path):
    seed, server, thread, base_url = _start_server(tmp_path)
    try:
        job_id = seed.create_job(
            {
                "delivery_id": "phase2-delivery",
                "goal": "synthetic remote client test",
            },
            repo="yzhlx/hermes-open-swe-smoke-test",
            role="coding_agent",
        )
        client = _client(base_url)
        client.register(name="phase2-codex-worker")

        snapshot = client.get_job(job_id)
        assert snapshot["id"] == job_id
        assert snapshot["state"] == "pending"

        claimed = client.claim_job(WORKER_TOKEN, job_id)
        assert claimed["job_id"] == job_id
        client.transition_job(
            WORKER_TOKEN,
            job_id,
            "CODEX_RUNNING",
            {"phase": "codex"},
        )
        client.append_event(job_id, {
            "id": "phase2-event-1",
            "type": "codex_result",
            "payload": {"exit_code": 0},
        })
        assert any(
            event["event_type"] == "codex_result"
            for event in client.get_events(job_id)
        )

        client.handoff_for_review(
            WORKER_TOKEN,
            job_id,
            event={
                "id": "phase2-pr-created",
                "type": "pr_created",
                "payload": {
                    "pr_number": 17,
                    "commit_sha": "a" * 40,
                },
            },
            result={
                "exit_code": 0,
                "pr_number": 17,
                "commit_sha": "a" * 40,
            },
        )
        handed_off = seed.get_job(job_id)
        assert handed_off["state"] == "agent_done"
        assert handed_off["lease_expires"] is None
        assert handed_off["pr_number"] == 17
    finally:
        _stop(seed, server, thread)


def test_replayed_nonce_and_unknown_worker_fail_closed(tmp_path):
    seed, server, thread, base_url = _start_server(tmp_path)
    try:
        nonce = lambda: "fixed-synthetic-nonce"
        now = int(time.time())
        client = _client(base_url, nonce_factory=nonce, clock=lambda: now)
        client.register(name="phase2-codex-worker")
        with pytest.raises(ControlPlaneError, match="replay_detected"):
            client.heartbeat(WORKER_TOKEN)

        unknown = _client(base_url, token="synthetic-unregistered-token")
        with pytest.raises(ControlPlaneError, match="worker_not_allowlisted"):
            unknown.register(name="unknown")
    finally:
        _stop(seed, server, thread)


def test_remote_token_broker_requires_same_client_identity_and_active_lease(tmp_path):
    broker = _RecordingBroker()
    seed, server, thread, base_url = _start_server(tmp_path, broker=broker)
    try:
        job_id = seed.create_job(
            {"delivery_id": "phase2-token"},
            repo="yzhlx/hermes-open-swe-smoke-test",
            role="coding_agent",
        )
        client = _client(base_url)
        client.register(name="phase2-codex-worker")
        remote_broker = RemoteTokenBroker(client)

        with pytest.raises(ControlPlaneError, match="job_not_owned_by_worker"):
            remote_broker.get_token_for_job(client, job_id, WORKER_TOKEN)

        client.claim_job(WORKER_TOKEN, job_id)
        token = remote_broker.get_token_for_job(client, job_id, WORKER_TOKEN)
        assert token == "ghs_FAKE_PHASE2_INSTALLATION_TOKEN"
        assert broker.calls == [job_id]
        persisted = json.dumps(
            {
                "job": seed.get_job(job_id),
                "events": seed.get_events(job_id),
            },
            sort_keys=True,
        )
        assert token not in persisted

        with pytest.raises(ControlPlaneError, match="worker_identity_mismatch"):
            remote_broker.get_token_for_job(client, job_id, OTHER_TOKEN)
    finally:
        _stop(seed, server, thread)


def test_non_host_worker_cannot_claim_privileged_job(tmp_path):
    seed, server, thread, base_url = _start_server(tmp_path)
    try:
        job_id = seed.create_job(
            {"delivery_id": "phase2-host-only"},
            repo="yzhlx/hermes-open-swe-smoke-test",
            role="coding_agent",
        )
        ordinary = _client(base_url, token=OTHER_TOKEN)
        ordinary.register(name="ordinary-worker")
        with pytest.raises(ControlPlaneError, match="host_worker_required"):
            ordinary.get_job(job_id)
        with pytest.raises(ControlPlaneError, match="host_worker_required"):
            ordinary.claim_job(OTHER_TOKEN, job_id)
        assert seed.get_job(job_id)["state"] == "pending"
        assert seed.get_job(job_id)["worker_token_hash"] is None
    finally:
        _stop(seed, server, thread)


def test_codex_job_runner_remote_transition_and_handoff_are_idempotent(tmp_path):
    seed, server, thread, base_url = _start_server(tmp_path)
    try:
        job_id = seed.create_job(
            {"delivery_id": "phase2-runner-integration"},
            repo="yzhlx/hermes-open-swe-smoke-test",
            role="coding_agent",
        )
        client = _client(base_url)
        client.register(name="phase2-codex-worker")
        client.claim_job(WORKER_TOKEN, job_id)
        runner = CodexJobRunner(
            control_plane=client,
            token_broker=object(),
            codex_runner=object(),
            repository_preparer=object(),
            git_operations=object(),
            docker_backend_factory=lambda _path: object(),
            github_client=object(),
            work_root=tmp_path / "worktrees",
        )
        runner._transition(
            job_id,
            WORKER_TOKEN,
            "CODEX_RUNNING",
            {"phase": "remote"},
        )
        for _ in range(2):
            result = runner._handoff_for_review(
                job_id,
                WORKER_TOKEN,
                repo_path=tmp_path,
                result_state="PR_CREATED",
                event_type="pr_created",
                event_payload={
                    "pr_number": 23,
                    "commit_sha": "d" * 40,
                },
                result={
                    "exit_code": 0,
                    "pr_number": 23,
                    "commit_sha": "d" * 40,
                },
                commit_sha="d" * 40,
                pr_number=23,
            )
            assert result.state == "PR_CREATED"
        events = seed.get_events(job_id)
        handoffs = [
            event for event in events
            if event["event_type"] == "pr_created"
        ]
        assert len(handoffs) == 1
        assert handoffs[0]["event_id"] == (
            f"host-handoff:{job_id}:pr_created:" + "d" * 40
        )
        assert seed.get_job(job_id)["state"] == "agent_done"
        assert seed.get_job(job_id)["lease_expires"] is None
    finally:
        _stop(seed, server, thread)


def test_http_client_error_never_echoes_response_secret(monkeypatch):
    import io
    import urllib.error

    body = json.dumps({
        "error": "Authorization: Bearer synthetic-sensitive-value"
    }).encode("utf-8")

    def fail(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://control.invalid/worker/register",
            403,
            "forbidden",
            {},
            io.BytesIO(body),
        )

    monkeypatch.setattr("urllib.request.urlopen", fail)
    client = ControlPlaneHttpClient(
        "https://control.invalid",
        WORKER_TOKEN,
    )
    with pytest.raises(ControlPlaneError) as caught:
        client.register(name="phase2-codex-worker")
    rendered = str(caught.value)
    assert "synthetic-sensitive-value" not in rendered
    assert "Bearer" not in rendered
    assert rendered == "remote_http_403"
