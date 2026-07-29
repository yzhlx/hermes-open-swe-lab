"""Fail-closed workbench contracts for merge and FINAL_ACCEPTANCE."""
from __future__ import annotations

import inspect
import json

import pytest

from hermes_worker.control_plane import ControlPlaneError
from hermes_worker.github_client import (
    FakeGitHubClient,
    GitHubClient,
    GitHubRestClient,
    RealGitHubClient,
)
from hermes_worker.reviewer import ReviewFinding, ReviewVerdict
from hermes_worker.scheduler import Scheduler


ALLOWED_REPO = "yzhlx/hermes-open-swe-smoke-test"
HEAD_SHA = "a" * 40
OTHER_SHA = "f" * 40


class ApproveClean:
    def review(self, pr_state, evidence):
        return ReviewVerdict("APPROVE", findings=[])


class ApproveWithBlocking:
    def review(self, pr_state, evidence):
        return ReviewVerdict(
            "APPROVE",
            findings=[
                ReviewFinding(
                    "blocking",
                    "Blocking evidence must override an APPROVE label",
                )
            ],
        )


def _scheduler_case(control_plane, *, job_sha=HEAD_SHA):
    job_id, _ = control_plane.create_issue_task(
        ALLOWED_REPO,
        700 + len(control_plane.list_jobs()),
        {"command": "acceptance contract"},
        role="coding_agent",
    )
    github = FakeGitHubClient()
    github.ensure_repo(ALLOWED_REPO)
    branch = f"hermes/task-{job_id}"
    github.push_branch(ALLOWED_REPO, branch, HEAD_SHA)
    pr = github.create_draft_pr(ALLOWED_REPO, branch, "contract PR")
    control_plane.update_job(
        job_id,
        pr_number=pr["number"],
        commit_sha=job_sha,
        round=1,
        ci_status="pending",
    )
    return job_id, github, Scheduler(control_plane, github, ALLOWED_REPO), pr


@pytest.mark.parametrize(
    "client_type",
    (GitHubClient, FakeGitHubClient, RealGitHubClient, GitHubRestClient),
)
def test_automation_github_surface_has_no_merge_capability(client_type):
    public_callables = {
        name
        for name, value in inspect.getmembers(client_type, predicate=callable)
        if not name.startswith("_")
    }
    forbidden = {
        name
        for name in public_callables
        if name in {"merge", "merge_pr", "enable_auto_merge"}
        or name.startswith("merge_")
    }
    assert forbidden == set(), (
        f"{client_type.__name__} exposes forbidden merge capability: "
        f"{sorted(forbidden)}. Automation must have no merge API, not merely "
        "a merge method that normally raises."
    )


def test_operator_action_surface_rejects_merge_without_side_effects(
    control_plane, require_method
):
    job_id = control_plane.create_job({"command": "no merge"})
    apply_action = require_method(control_plane, "apply_operator_action")
    before = control_plane.get_job(job_id)
    before_events = list(control_plane.get_events(job_id))

    with pytest.raises(ControlPlaneError):
        apply_action(job_id, "merge")

    assert control_plane.get_job(job_id) == before
    assert control_plane.get_events(job_id) == before_events


def test_direct_set_state_cannot_forge_final_acceptance(control_plane):
    job_id = control_plane.create_job({"command": "no forged acceptance"})

    with pytest.raises(ControlPlaneError):
        control_plane.set_state(job_id, "FINAL_ACCEPTANCE")

    assert control_plane.get_job(job_id)["state"] == "pending"


def test_free_form_payloads_are_redacted_before_storage_and_event_echo(
    control_plane,
):
    synthetic_token = "ghs_" + "SYNTHETIC_CREDENTIAL_MATERIAL_123456"
    private_key_body = "SYNTHETIC-PRIVATE-MATERIAL-MUST-NOT-PERSIST"
    synthetic_private_key = (
        "-----BEGIN " + "PRIVATE KEY-----\n"
        + private_key_body
        + "\n-----END " + "PRIVATE KEY-----"
    )
    job_id, created = control_plane.create_job_idempotent(
        {
            "goal": f"Keep ordinary goal text; token={synthetic_token}",
            "scope": {"note": synthetic_private_key},
            "acceptance": ["ordinary acceptance text"],
        },
        task_id="redaction-contract",
        repo=ALLOWED_REPO,
        role="coding_agent",
    )
    assert created is True
    control_plane.append_event(job_id, {
        "type": "review",
        "payload": {
            "summary": f"Review included {synthetic_token}",
            "findings": [{"detail": synthetic_private_key}],
        },
        "source_type": "scheduler",
        "source_id": "scheduler",
        "actor_role": "reviewer",
    })
    control_plane.apply_operator_action(
        job_id,
        "supplement",
        request_id="redaction-supplement",
        expected_version=0,
        reason=f"reason token={synthetic_token}",
        requirements=[
            "preserve ordinary requirement text",
            synthetic_private_key,
        ],
    )

    persisted = json.dumps(
        {
            "job": control_plane.get_job(job_id),
            "events": control_plane.get_events(job_id),
        },
        ensure_ascii=False,
    )
    assert synthetic_token not in persisted
    assert private_key_body not in persisted
    assert "ordinary goal text" in persisted
    assert "ordinary acceptance text" in persisted
    assert "preserve ordinary requirement text" in persisted
    assert "REDACTED" in persisted



def test_request_ids_and_worker_terminal_text_are_redacted_before_storage(
    control_plane,
    worker_token,
):
    synthetic_token = "ghs_" + "SYNTHETIC_DURABLE_MARKER_123456789"

    operator_job = control_plane.create_job({"command": "operator redaction"})
    action_result = control_plane.apply_operator_action(
        operator_job,
        "pause",
        request_id=synthetic_token,
        expected_version=0,
    )
    replay_result = control_plane.apply_operator_action(
        operator_job,
        "pause",
        request_id=synthetic_token,
        expected_version=0,
    )
    assert replay_result == action_result

    completed_job = control_plane.create_job({"command": "complete redaction"})
    control_plane.claim_job(worker_token, completed_job)
    control_plane.complete(
        worker_token,
        completed_job,
        {
            "command": f"run {synthetic_token}",
            "modified_files": [f"artifact-{synthetic_token}.txt"],
        },
    )

    failed_job = control_plane.create_job({"command": "failure redaction"})
    control_plane.claim_job(worker_token, failed_job)
    control_plane.fail(
        worker_token,
        failed_job,
        f"failure included {synthetic_token}",
    )

    operator_actions = [
        dict(row)
        for row in control_plane.conn.execute(
            "SELECT request_id, result FROM operator_actions"
        ).fetchall()
    ]
    persisted = json.dumps(
        {
            "action_result": action_result,
            "operator_actions": operator_actions,
            "operator_events": control_plane.get_events(operator_job),
            "completed_job": control_plane.get_job(completed_job),
            "failed_job": control_plane.get_job(failed_job),
        },
        ensure_ascii=False,
    )
    assert synthetic_token not in persisted
    assert "REDACTED" in persisted


def test_approve_with_blocking_finding_never_awaits_user(control_plane):
    job_id, github, scheduler, pr = _scheduler_case(control_plane)
    github.set_ci_status(pr["number"], "success")

    signal = scheduler.review_phase(
        job_id, "success", ApproveWithBlocking()
    )

    assert signal["action"] != "await_user"
    assert control_plane.get_job(job_id)["state"] != "await_user"


def test_wrong_job_sha_never_reaches_acceptance(control_plane):
    job_id, github, scheduler, pr = _scheduler_case(
        control_plane, job_sha=OTHER_SHA
    )
    github.set_ci_status(pr["number"], "success")
    assert github.get_pr(pr["number"])["head_sha"] == HEAD_SHA

    signal = scheduler.review_phase(job_id, "success", ApproveClean())

    assert signal["action"] != "await_user"
    assert control_plane.get_job(job_id)["state"] != "await_user"


def test_caller_claimed_success_cannot_override_real_pending_ci(control_plane):
    job_id, github, scheduler, pr = _scheduler_case(control_plane)
    assert github.get_ci_status(pr["number"]) == "pending"

    signal = scheduler.review_phase(job_id, "success", ApproveClean())

    assert signal["action"] != "await_user"
    assert control_plane.get_job(job_id)["state"] != "await_user"


@pytest.mark.parametrize(
    "ci_status",
    (None, "", "queued", "pending", "failure", "cancelled", "timed_out"),
)
def test_non_success_ci_never_reaches_acceptance(
    control_plane, ci_status
):
    job_id, _, scheduler, _ = _scheduler_case(control_plane)

    signal = scheduler.review_phase(job_id, ci_status, ApproveClean())

    assert signal["action"] != "await_user"
    assert control_plane.get_job(job_id)["state"] != "await_user"
