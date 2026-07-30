from __future__ import annotations

from copy import deepcopy
import json

import pytest

from hermes_workbench.operator_api import _event_channel, _event_view
from hermes_worker.control_plane import ControlPlane, ControlPlaneError
from hermes_worker.db import hash_token


SMOKE_REPO = "yzhlx/hermes-open-swe-smoke-test"


def _new_job(
    control_plane: ControlPlane,
    *,
    task_id: str,
    role: str = "coding",
) -> int:
    job_id, created = control_plane.create_job_idempotent(
        {
            "goal": "Exercise the trusted workbench contract.",
            "scope": ["tests/workbench"],
            "acceptance": ["The contract is enforced."],
        },
        task_id,
        repo=SMOKE_REPO,
        role=role,
        model="test-model",
    )
    assert created is True
    return job_id


def _register_and_claim(
    control_plane: ControlPlane,
    worker_token: str,
    job_id: int,
) -> str:
    registration = control_plane.register(worker_token)
    control_plane.claim_job(worker_token, job_id)
    return registration["worker_id"]


def _event_by_id(
    control_plane: ControlPlane,
    job_id: int,
    event_id: str,
) -> dict:
    events = control_plane.get_events_after(job_id, 0, 1_000)
    return next(
        event
        for event in events
        if event.get("event_id", event.get("id")) == event_id
    )


def _snapshot(control_plane: ControlPlane, job_id: int) -> dict:
    return deepcopy(
        {
            "jobs": control_plane.list_jobs(),
            "projection": control_plane.get_task_projection(job_id, 60),
            "events": control_plane.get_events_after(job_id, 0, 1_000),
        }
    )


def test_worker_event_provenance_is_derived_from_authenticated_identity(
    control_plane: ControlPlane,
    worker_token: str,
) -> None:
    job_id = _new_job(control_plane, task_id="trusted-provenance")
    worker_id = _register_and_claim(control_plane, worker_token, job_id)

    result = control_plane.post_events(
        worker_token,
        job_id,
        [
            {
                "id": "spoofed-provenance",
                "type": "progress",
                "source_type": "github",
                "source_id": "fake",
                "payload": {
                    "message": "Real implementation progress.",
                    "source_type": "github",
                    "source_id": "fake",
                    "actor_role": "human_owner",
                    "role": "human_owner",
                    "channel": "user-action-required",
                },
            }
        ],
    )

    assert result == {"ok": True, "accepted": 1}
    stored = _event_by_id(control_plane, job_id, "spoofed-provenance")
    projected = _event_view(stored)

    assert stored["source_type"] == "worker"
    assert stored["source_id"] == worker_id
    assert projected["source_type"] == "worker"
    assert projected["source_id"] == worker_id
    assert projected["actor_role"] == "coding"
    assert projected["channel"] == "implementation"

    serialized = json.dumps(
        {"stored": stored, "projected": projected},
        sort_keys=True,
    )
    for spoofed_value in (
        '"github"',
        '"fake"',
        '"human_owner"',
        '"user-action-required"',
    ):
        assert spoofed_value not in serialized


def test_legacy_event_is_unverified_and_not_projected_as_agent_message(
    control_plane: ControlPlane,
) -> None:
    job_id = _new_job(control_plane, task_id="legacy-unverified")
    control_plane.append_event(
        job_id,
        {
            "id": "legacy-event",
            "type": "progress",
            "payload": {
                "message": "Imported legacy event.",
                "actor_role": "coding",
                "channel": "implementation",
            },
        },
    )

    stored = _event_by_id(control_plane, job_id, "legacy-event")
    projected = _event_view(stored)

    assert stored["source_type"] == "legacy"
    assert stored["source_id"] == "unverified"
    assert projected["source_type"] == "legacy"
    assert projected["source_id"] == "unverified"
    assert projected["actor_role"] is None
    assert projected["unverified_display"] is True


def test_task_idempotency_replays_identical_request_and_rejects_conflicts(
    control_plane: ControlPlane,
) -> None:
    task_id = "idempotent-task"
    payload = {
        "goal": "Add a trusted workbench event.",
        "scope": ["operator API", "event store"],
        "acceptance": ["Provenance is server-derived."],
    }
    call = {
        "payload": payload,
        "task_id": task_id,
        "repo": SMOKE_REPO,
        "role": "coding",
        "model": "test-model",
    }

    job_id, created = control_plane.create_job_idempotent(**call)
    replay_job_id, replay_created = control_plane.create_job_idempotent(
        **deepcopy(call)
    )

    assert created is True
    assert (replay_job_id, replay_created) == (job_id, False)
    baseline = _snapshot(control_plane, job_id)

    conflicts = [
        {**call, "repo": f"{SMOKE_REPO} "},
        {**call, "payload": {**payload, "goal": "A changed goal."}},
        {**call, "payload": {**payload, "scope": ["changed scope"]}},
        {
            **call,
            "payload": {
                **payload,
                "acceptance": ["A changed acceptance criterion."],
            },
        },
    ]
    for conflict in conflicts:
        with pytest.raises(ControlPlaneError):
            control_plane.create_job_idempotent(**conflict)
        assert _snapshot(control_plane, job_id) == baseline


def test_operator_request_id_replay_is_content_addressed_and_side_effect_free(
    control_plane: ControlPlane,
) -> None:
    job_id = _new_job(control_plane, task_id="operator-idempotency")
    before = control_plane.get_task_projection(job_id, 60)
    request = {
        "job_id": job_id,
        "action": "supplement",
        "request_id": "operator-request-1",
        "expected_version": before["version"],
        "reason": "Clarify the acceptance contract.",
        "requirements": ["Keep provenance server-derived."],
    }

    original = control_plane.apply_operator_action(**request)
    stable = _snapshot(control_plane, job_id)

    # An identical replay returns the persisted result even when the original
    # expected version is now stale.
    assert control_plane.apply_operator_action(**deepcopy(request)) == original
    assert _snapshot(control_plane, job_id) == stable

    conflicts = [
        {**request, "action": "pause"},
        {**request, "reason": "A different reason."},
        {**request, "requirements": ["A different requirement."]},
    ]
    for conflict in conflicts:
        with pytest.raises(ControlPlaneError):
            control_plane.apply_operator_action(**conflict)
        assert _snapshot(control_plane, job_id) == stable


def test_worker_cannot_spoof_another_registered_worker_source_id(
    tmp_path,
) -> None:
    first_token = "trusted-worker-one"
    second_token = "trusted-worker-two"
    control_plane = ControlPlane(
        str(tmp_path / "two-workers.sqlite3"),
        allowed_token_hashes={
            hash_token(first_token),
            hash_token(second_token),
        },
    )
    first_worker_id = control_plane.register(first_token)["worker_id"]
    second_worker_id = control_plane.register(second_token)["worker_id"]
    job_id = _new_job(control_plane, task_id="cross-worker-spoof")
    control_plane.claim_job(first_token, job_id)

    control_plane.post_events(
        first_token,
        job_id,
        [
            {
                "id": "cross-worker-spoof-event",
                "type": "progress",
                "source_type": "worker",
                "source_id": second_worker_id,
                "payload": {
                    "message": "Authenticated worker event.",
                    "source_id": second_worker_id,
                },
            }
        ],
    )

    stored = _event_by_id(
        control_plane,
        job_id,
        "cross-worker-spoof-event",
    )
    projected = _event_view(stored)
    assert stored["source_id"] == first_worker_id
    assert projected["source_id"] == first_worker_id
    assert second_worker_id not in json.dumps(
        {"stored": stored, "projected": projected},
        sort_keys=True,
    )


def test_server_channel_mapping_is_exhaustive_and_unknown_is_unverified(
    control_plane: ControlPlane,
    worker_token: str,
) -> None:
    expected_channels = {
        "plan_created": "planning",
        "progress": "implementation",
        "review": "review",
        "ci_passed": "qa",
        "pr_created": "release",
        "task_created": "control-room",
        "await_user": "user-action-required",
    }
    for event_type, expected_channel in expected_channels.items():
        assert (
            _event_channel(
                event_type,
                {"channel": "client-spoofed-channel"},
            )
            == expected_channel
        )

    job_id = _new_job(control_plane, task_id="unknown-event-channel")
    _register_and_claim(control_plane, worker_token, job_id)
    control_plane.post_events(
        worker_token,
        job_id,
        [
            {
                "id": "unknown-event",
                "type": "future_unknown_event",
                "payload": {
                    "message": "Opaque machine event.",
                    "channel": "review",
                    "actor_role": "human_owner",
                },
            }
        ],
    )
    projected = _event_view(
        _event_by_id(control_plane, job_id, "unknown-event")
    )

    assert projected["raw_type"] == "future_unknown_event"
    assert projected["channel"] == "control-room"
    assert projected["unverified_display"] is True
    assert projected["actor_role"] == "coding"
    assert projected["payload"]["message"] == "Opaque machine event."
    assert "dialogue" not in projected["payload"]
    assert "speaker" not in projected["payload"]
