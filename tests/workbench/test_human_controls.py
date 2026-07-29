"""Strong human-control contracts for ``ControlPlane.apply_operator_action``."""
from __future__ import annotations

import json

import pytest

from hermes_worker.control_plane import ControlPlaneError


ALLOWED_REPO = "yzhlx/hermes-open-swe-smoke-test"
COMMIT_SHA = "a" * 40


def _create_job(control_plane, issue_number: int, state: str) -> int:
    job_id, created = control_plane.create_issue_task(
        ALLOWED_REPO,
        issue_number,
        {"command": "human control contract", "pr": {"draft": True}},
        role="coding_agent",
        task_id=f"human-control-{issue_number}",
    )
    assert created is True
    control_plane.update_job(
        job_id,
        state=state,
        pr_number=issue_number,
        commit_sha=COMMIT_SHA,
        ci_status="success",
    )
    return job_id


def _version(job: dict) -> int:
    assert "version" in job, "WORKBENCH_CONTRACT_MISSING: jobs.version is required"
    return int(job["version"])


def _events(control_plane, job_id: int, event_type: str) -> list:
    return [
        event
        for event in control_plane.get_events(job_id)
        if event["event_type"] == event_type
    ]


def _payload(event: dict) -> dict:
    value = event.get("payload") or "{}"
    return json.loads(value) if isinstance(value, str) else value


def test_supplement_requires_requirements_and_replay_is_side_effect_free(
    control_plane,
):
    job_id = _create_job(control_plane, 801, "agent_done")
    before = control_plane.get_job(job_id)
    before_events = list(control_plane.get_events(job_id))

    with pytest.raises(ControlPlaneError):
        control_plane.apply_operator_action(
            job_id,
            "supplement",
            request_id="supplement-empty",
            expected_version=_version(before),
            requirements=[],
        )
    assert control_plane.get_job(job_id) == before
    assert control_plane.get_events(job_id) == before_events

    result = control_plane.apply_operator_action(
        job_id,
        "supplement",
        request_id="supplement-801",
        expected_version=_version(before),
        requirements=["Use the current PR and add an empty-state test."],
    )
    after = control_plane.get_job(job_id)
    assert after["requirements_revision"] == (
        (before.get("requirements_revision") or 0) + 1
    )
    for field in ("state", "task_id", "pr_number", "commit_sha", "payload"):
        assert after[field] == before[field]
    assert _version(after) == _version(before) + 1
    [event] = _events(control_plane, job_id, "operator_requirements_added")
    assert _payload(event)["requirements"] == [
        "Use the current PR and add an empty-state test."
    ]

    event_snapshot = list(control_plane.get_events(job_id))
    replay = control_plane.apply_operator_action(
        job_id,
        "supplement",
        request_id="supplement-801",
        expected_version=_version(before),
        requirements=["Use the current PR and add an empty-state test."],
    )
    assert replay == result
    assert control_plane.get_job(job_id) == after
    assert control_plane.get_events(job_id) == event_snapshot


def test_reject_allowed_states_return_same_delivery_to_agent_and_wrong_state_is_atomic(
    control_plane,
):
    for offset, allowed_state in enumerate(
        ("await_user", "ready_for_manual_merge")
    ):
        job_id = _create_job(control_plane, 810 + offset, allowed_state)
        before = control_plane.get_job(job_id)
        control_plane.apply_operator_action(
            job_id,
            "reject",
            request_id=f"reject-{allowed_state}",
            expected_version=_version(before),
            reason="Acceptance evidence is incomplete.",
        )
        after = control_plane.get_job(job_id)
        assert after["state"] == "agent_done"
        for field in ("task_id", "pr_number", "commit_sha", "payload"):
            assert after[field] == before[field]
        assert _version(after) == _version(before) + 1
        [event] = _events(control_plane, job_id, "operator_rejected")
        assert _payload(event)["reason"] == "Acceptance evidence is incomplete."

    wrong_job = _create_job(control_plane, 819, "pending")
    wrong_before = control_plane.get_job(wrong_job)
    wrong_events = list(control_plane.get_events(wrong_job))
    with pytest.raises(ControlPlaneError):
        control_plane.apply_operator_action(
            wrong_job,
            "reject",
            request_id="reject-wrong-state",
            expected_version=_version(wrong_before),
            reason="Must not reject from pending.",
        )
    assert control_plane.get_job(wrong_job) == wrong_before
    assert control_plane.get_events(wrong_job) == wrong_events


def test_approve_only_from_await_user_preserves_draft_pr_and_never_completes(
    control_plane,
):
    job_id = _create_job(control_plane, 821, "await_user")
    before = control_plane.get_job(job_id)
    result = control_plane.apply_operator_action(
        job_id,
        "approve",
        request_id="approve-821",
        expected_version=_version(before),
        reason="Human acceptance granted; merge remains manual.",
    )
    after = control_plane.get_job(job_id)
    assert after["state"] == "ready_for_manual_merge"
    assert after["state"] not in {"completed", "merged"}
    for field in ("task_id", "pr_number", "commit_sha", "payload"):
        assert after[field] == before[field]
    assert json.loads(after["payload"])["pr"]["draft"] is True
    assert _version(after) == _version(before) + 1
    [event] = _events(control_plane, job_id, "operator_approved")
    assert _payload(event)["reason"] == (
        "Human acceptance granted; merge remains manual."
    )

    event_snapshot = list(control_plane.get_events(job_id))
    replay = control_plane.apply_operator_action(
        job_id,
        "approve",
        request_id="approve-821",
        expected_version=_version(before),
        reason="Human acceptance granted; merge remains manual.",
    )
    assert replay == result
    assert control_plane.get_job(job_id) == after
    assert control_plane.get_events(job_id) == event_snapshot

    wrong_job = _create_job(control_plane, 822, "agent_done")
    wrong_before = control_plane.get_job(wrong_job)
    wrong_events = list(control_plane.get_events(wrong_job))
    with pytest.raises(ControlPlaneError):
        control_plane.apply_operator_action(
            wrong_job,
            "approve",
            request_id="approve-wrong-state",
            expected_version=_version(wrong_before),
        )
    assert control_plane.get_job(wrong_job) == wrong_before
    assert control_plane.get_events(wrong_job) == wrong_events


def test_version_conflict_is_atomic_but_idempotent_replay_wins(
    control_plane,
):
    job_id = _create_job(control_plane, 831, "agent_done")
    initial = control_plane.get_job(job_id)
    initial_version = _version(initial)
    first = control_plane.apply_operator_action(
        job_id,
        "supplement",
        request_id="version-first",
        expected_version=initial_version,
        requirements=["First accepted requirement."],
    )
    after_first = control_plane.get_job(job_id)
    assert _version(after_first) == initial_version + 1

    conflict_snapshot = dict(after_first)
    conflict_events = list(control_plane.get_events(job_id))
    with pytest.raises(ControlPlaneError):
        control_plane.apply_operator_action(
            job_id,
            "supplement",
            request_id="version-conflict",
            expected_version=initial_version,
            requirements=["This request uses a stale version."],
        )
    assert control_plane.get_job(job_id) == conflict_snapshot
    assert control_plane.get_events(job_id) == conflict_events

    replay = control_plane.apply_operator_action(
        job_id,
        "supplement",
        request_id="version-first",
        expected_version=initial_version,
        requirements=["First accepted requirement."],
    )
    assert replay == first
    assert control_plane.get_job(job_id) == conflict_snapshot
    assert control_plane.get_events(job_id) == conflict_events

    second = control_plane.apply_operator_action(
        job_id,
        "supplement",
        request_id="version-second",
        expected_version=initial_version + 1,
        requirements=["Second accepted requirement."],
    )
    assert second != first
    assert _version(control_plane.get_job(job_id)) == initial_version + 2


def test_merge_auto_merge_and_complete_are_forbidden_without_side_effects(
    control_plane,
):
    job_id = _create_job(control_plane, 841, "await_user")
    original_job = control_plane.get_job(job_id)
    original_events = list(control_plane.get_events(job_id))
    original_version = _version(original_job)

    for action in ("merge", "auto_merge", "complete"):
        with pytest.raises(ControlPlaneError):
            control_plane.apply_operator_action(
                job_id,
                action,
                request_id=f"forbidden-{action}",
                expected_version=original_version,
                reason="Forbidden automation action.",
            )
        assert control_plane.get_job(job_id) == original_job
        assert control_plane.get_events(job_id) == original_events
        assert _version(control_plane.get_job(job_id)) == original_version
