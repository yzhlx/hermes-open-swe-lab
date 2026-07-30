"""Operator pause/resume contracts against the real ControlPlane fixture."""
from __future__ import annotations


def _event_count(control_plane, job_id: int, event_type: str) -> int:
    return sum(
        event["event_type"] == event_type
        for event in control_plane.get_events(job_id)
    )


def test_pending_pause_blocks_claim_and_resume_is_idempotent(
    control_plane, worker_token, require_method
):
    job_id = control_plane.create_job({"command": "pause contract"})
    apply_action = require_method(control_plane, "apply_operator_action")
    get_projection = require_method(control_plane, "get_task_projection")

    apply_action(job_id, "pause")
    apply_action(job_id, "pause")

    paused = get_projection(job_id, 30)
    assert paused["state"] == "pending"
    assert paused["control_state"] == "paused"
    assert control_plane.claim(worker_token) == {"empty": True}, (
        "A pending but paused task must not be claimable."
    )
    assert _event_count(control_plane, job_id, "pause_requested") == 1
    assert _event_count(control_plane, job_id, "paused") == 1

    apply_action(job_id, "resume")
    apply_action(job_id, "resume")

    resumed = get_projection(job_id, 30)
    assert resumed["state"] == "pending"
    assert resumed["control_state"] == "active"
    assert _event_count(control_plane, job_id, "resumed") == 1
    claimed = control_plane.claim(worker_token)
    assert claimed["job_id"] == job_id
