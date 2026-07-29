from __future__ import annotations

import io
import json
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

from hermes_worker.pi_cli_runner import AgentRunResult
from hermes_worker.host_agent_job_runner import (
    HostAgentJobRunner,
    _job_allowed_paths,
    _load_broker,
)
from hermes_worker.control_plane import ControlPlane, ControlPlaneError
from hermes_worker.db import hash_token
from hermes_worker.docker_sandbox import DockerTestResult
from hermes_worker.github_app import FakeAppApiClient, GitHubAppTokenBroker
from hermes_worker.github_client import GitHubRestClient
from hermes_worker.repository import (
    CommandResult,
    CommitResult,
    RepositoryPrepareResult,
    RepositoryPreparer,
)
from hermes_worker.reviewer import ReviewFinding, Reviewer, ReviewVerdict
from hermes_worker.scheduler import Scheduler


REPO = "yzhlx/hermes-open-swe-smoke-test"
WORKER_TOKEN = "offline-worker-token"
OTHER_WORKER_TOKEN = "other-offline-worker-token"
FAKE_INSTALLATION_TOKEN = "ghs_FAKE_INSTALLATION_TOKEN_123456"
SYNTHETIC_PRIVATE_KEY = (
    "-----BEGIN " + "PRIVATE KEY-----\n"
    "SYNTHETIC-TEST-MATERIAL-MUST-NOT-PERSIST\n"
    "-----END " + "PRIVATE KEY-----"
)


class RecordingBroker:
    def __init__(self):
        self.calls = 0

    def get_token_for_job(self, control_plane, job_id, worker_token):
        self.calls += 1
        return FAKE_INSTALLATION_TOKEN


class RecordingPreparer:
    def __init__(self):
        self.calls: list[dict] = []

    def prepare(
        self,
        destination,
        repo,
        base,
        branch,
        token,
        *,
        resume_existing_branch=False,
    ):
        destination = Path(destination)
        destination.mkdir(parents=True)
        self.calls.append(
            {
                "repo": repo,
                "base": base,
                "branch": branch,
                "resume_existing_branch": resume_existing_branch,
            }
        )
        return RepositoryPrepareResult(
            ok=True,
            repo_path=destination,
            exit_code=0,
            stderr_summary="",
            commands=["git init", "git fetch", "git checkout"],
            askpass_cleaned=True,
            token_cleared=True,
            cleaned_on_failure=False,
        )


class RecordingAgent:
    def __init__(self):
        self.prompts: list[str] = []

    def run(self, repo_path, prompt, timeout_seconds):
        self.prompts.append(prompt)
        return AgentRunResult(
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
            events_jsonl_path=str(Path(repo_path) / ".hermes" / "events.jsonl"),
            final_message_path=str(Path(repo_path) / ".hermes" / "final.txt"),
            stdout_summary="events=1",
            stderr_summary="",
            changed_files=["feature.py"],
            command_redacted="agent exec --sandbox workspace-write ...",
        )


class PassingDocker:
    def run_tests(self, command, timeout=1200):
        return DockerTestResult(
            image_id="sha256:image",
            container_id="container-id",
            command=command,
            exit_code=0,
            passed=1,
            failed=0,
            skipped=0,
            timed_out=False,
            stdout_summary="1 passed",
            stderr_summary="",
            cleanup_succeeded=True,
            residual_container_count=0,
        )


class RecordingGitOperations:
    def __init__(self):
        self.commit_calls = 0
        self.pushes: list[str] = []

    def changed_files(self, repo_path):
        return ["feature.py"]

    def commit(self, repo_path, message):
        self.commit_calls += 1
        return CommitResult(
            True,
            f"{self.commit_calls:040x}",
            0,
            "",
        )

    def push(self, repo_path, branch, token):
        self.pushes.append(branch)
        return CommandResult(0, "", "")


class RecordingGitHub:
    def __init__(self):
        self.pr_calls = 0
        self.head_sha = None
        self.labels: list[str] = []

    def create_draft_pr(self, repo, branch, base, title, body, token):
        self.pr_calls += 1
        return {
            "number": 12,
            "url": "https://example.invalid/pr/12",
            "draft": True,
        }

    def get_pr(self, pr_number):
        return {
            "number": pr_number,
            "head_sha": self.head_sha,
            "draft": True,
        }

    def get_ci_status(self, pr_number, expected_head=None):
        if expected_head is not None and self.head_sha != expected_head:
            return "pending"
        return "success"

    def add_label(self, pr_number, label):
        self.labels.append(label)


class CrashAfterDraftPrGitHub(RecordingGitHub):
    def create_draft_pr(self, repo, branch, base, title, body, token):
        self.pr_calls += 1
        raise KeyboardInterrupt("synthetic crash after remote side effect")


class JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _event_payload(event: dict) -> dict:
    return json.loads(event["payload"] or "{}")


def test_host_runner_blocks_changes_outside_job_allowed_paths():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        control_plane = ControlPlane(
            str(root / "jobs.sqlite"),
            allowed_token_hashes={hash_token(WORKER_TOKEN)},
        )
        control_plane.register(WORKER_TOKEN, "allowlist-agent")
        job_id = control_plane.create_job(
            {
                "delivery_id": "allowlist-delivery",
                "allowed_paths": ["automation-smoke-test/README.md"],
            },
            task_id="allowlist-task",
            repo=REPO,
            role="coding_agent",
        )
        broker = RecordingBroker()
        git = RecordingGitOperations()
        github = RecordingGitHub()
        runner = HostAgentJobRunner(
            control_plane=control_plane,
            token_broker=broker,
            agent_runner=RecordingAgent(),
            repository_preparer=RecordingPreparer(),
            git_operations=git,
            docker_backend_factory=lambda _repo_path: (_ for _ in ()).throw(
                AssertionError("Docker must not run for out-of-allowlist changes")
            ),
            github_client=github,
            work_root=root / "work",
        )

        result = runner.run(
            job_id,
            WORKER_TOKEN,
            REPO,
            "main",
            "change only the approved smoke file",
            "allowlist-delivery",
            "python scripts/validate_smoke_contract.py --self-test",
            60,
        )

        assert result.state == "BLOCKED"
        assert result.error == "changed_files_outside_job_allowlist"
        assert git.commit_calls == 0
        assert git.pushes == []
        assert github.pr_calls == 0
        assert broker.calls == 1
        control_plane.conn.close()


@pytest.mark.parametrize(
    "value",
    [
        [],
        ["../outside"],
        ["/absolute"],
        [r"windows\\path"],
        ["C:/absolute"],
        ["automation-smoke-test//README.md"],
        ["automation-smoke-test/./README.md"],
        [".git/config"],
    ],
)
def test_job_allowed_paths_rejects_noncanonical_or_protected_values(value):
    with pytest.raises(ControlPlaneError, match="job_allowed_paths_invalid"):
        _job_allowed_paths({"allowed_paths": value})


def test_job_allowed_paths_accepts_exact_canonical_relative_file():
    assert _job_allowed_paths({
        "allowed_paths": ["automation-smoke-test/README.md"],
    }) == frozenset({"automation-smoke-test/README.md"})


def test_host_runner_allows_exact_job_allowed_path():
    class AllowedGit(RecordingGitOperations):
        def changed_files(self, repo_path):
            return ["automation-smoke-test/README.md"]

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        control_plane = ControlPlane(
            str(root / "jobs.sqlite"),
            allowed_token_hashes={hash_token(WORKER_TOKEN)},
        )
        try:
            control_plane.register(WORKER_TOKEN, "allowlist-agent")
            job_id = control_plane.create_job(
                {
                    "delivery_id": "allowlist-positive",
                    "allowed_paths": ["automation-smoke-test/README.md"],
                },
                task_id="allowlist-positive-task",
                repo=REPO,
                role="coding_agent",
            )
            git = AllowedGit()
            github = RecordingGitHub()
            runner = HostAgentJobRunner(
                control_plane=control_plane,
                token_broker=RecordingBroker(),
                agent_runner=RecordingAgent(),
                repository_preparer=RecordingPreparer(),
                git_operations=git,
                docker_backend_factory=lambda _repo_path: PassingDocker(),
                github_client=github,
                work_root=root / "work",
            )

            result = runner.run(
                job_id,
                WORKER_TOKEN,
                REPO,
                "main",
                "change only the approved smoke file",
                "allowlist-positive",
                "python scripts/validate_smoke_contract.py --self-test",
                60,
            )

            assert result.state == "PR_CREATED"
            assert git.commit_calls == 1
            assert len(git.pushes) == 1
            assert github.pr_calls == 1
        finally:
            control_plane.conn.close()


def test_host_runner_round2_reuses_job_branch_and_draft_pr_with_review_feedback():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        control_plane = ControlPlane(
            str(root / "jobs.sqlite"),
            allowed_token_hashes={hash_token(WORKER_TOKEN)},
        )
        control_plane.register(WORKER_TOKEN, "same-coding-agent")
        job_id = control_plane.create_job(
            {"delivery_id": "delivery-1"},
            task_id="task-1",
            repo=REPO,
            role="coding_agent",
        )
        broker = RecordingBroker()
        preparer = RecordingPreparer()
        agent = RecordingAgent()
        git = RecordingGitOperations()
        github = RecordingGitHub()
        runner = HostAgentJobRunner(
            control_plane=control_plane,
            token_broker=broker,
            agent_runner=agent,
            repository_preparer=preparer,
            git_operations=git,
            docker_backend_factory=lambda repo_path: PassingDocker(),
            github_client=github,
            work_root=root / "work",
        )

        first = runner.run(
            job_id,
            WORKER_TOKEN,
            REPO,
            "main",
            "implement the feature",
            "delivery-1",
            "pytest -q",
            60,
        )
        first_job = control_plane.get_job(job_id)
        assert first.state == "PR_CREATED"
        assert first_job["state"] == "agent_done"
        assert first_job["ended_at"] is None
        assert first_job["pr_number"] == 12
        first_sha = first_job["commit_sha"]
        first_event_count = len(control_plane.get_events(job_id))

        replay = runner.run(
            job_id,
            WORKER_TOKEN,
            REPO,
            "main",
            "implement the feature",
            "delivery-1",
            "pytest -q",
            60,
        )
        assert replay.state == "PR_CREATED"
        assert replay.commit_sha == first_sha
        assert len(agent.prompts) == 1
        assert git.commit_calls == 1
        assert len(git.pushes) == 1
        assert github.pr_calls == 1
        assert broker.calls == 3
        assert len(control_plane.get_events(job_id)) == first_event_count

        github.head_sha = first_sha

        review_calls = 0

        def decide(pr_state, evidence):
            nonlocal review_calls
            review_calls += 1
            if review_calls == 1:
                return ReviewVerdict(
                    # A contradictory Reviewer response must fail closed to
                    # REQUEST_CHANGES because it still contains a blocker.
                    "APPROVE",
                    findings=[
                        ReviewFinding(
                            "blocking",
                            "Handle empty input",
                            "Return an explicit empty state instead of raising; "
                            f"ignore pasted token {FAKE_INSTALLATION_TOKEN}.\n"
                            f"{SYNTHETIC_PRIVATE_KEY}",
                        )
                    ],
                    summary="One blocking correctness issue.",
                )
            return ReviewVerdict("APPROVE", summary="Rework verified.")

        reviewer = Reviewer(decide)
        scheduler = Scheduler(control_plane, github, REPO)
        signal = scheduler.review_phase(job_id, "success", reviewer)
        assert signal["action"] == "rework"

        review_event = [
            event
            for event in control_plane.get_events(job_id)
            if event["event_type"] == "review"
        ][-1]
        feedback = _event_payload(review_event)
        assert feedback["verdict"] == "REQUEST_CHANGES"
        assert feedback["summary"] == "One blocking correctness issue."
        assert feedback["findings"] == [
            {
                "severity": "blocking",
                "title": "Handle empty input",
                "detail": (
                    "Return an explicit empty state instead of raising; "
                    "ignore pasted token ghs_***REDACTED***.\n"
                    "[REDACTED PRIVATE KEY BLOCK]"
                ),
            }
        ]
        stored_events = json.dumps(control_plane.get_events(job_id))
        assert FAKE_INSTALLATION_TOKEN not in stored_events
        assert "SYNTHETIC-TEST-MATERIAL-MUST-NOT-PERSIST" not in stored_events

        second = runner.run(
            job_id,
            WORKER_TOKEN,
            REPO,
            "main",
            "implement the feature",
            "delivery-1",
            "pytest -q",
            60,
        )
        second_job = control_plane.get_job(job_id)
        second_sha = second_job["commit_sha"]

        assert second.state == "PR_UPDATED"
        assert second.pr_number == first.pr_number == 12
        assert second_job["state"] == "agent_done"
        assert second_job["ended_at"] is None
        assert second_sha != first_sha
        assert github.pr_calls == 1
        assert len(git.pushes) == 2
        assert preparer.calls[0]["branch"] == preparer.calls[1]["branch"]
        assert preparer.calls[0]["resume_existing_branch"] is False
        assert preparer.calls[1]["resume_existing_branch"] is True
        assert "Handle empty input" in agent.prompts[1]
        assert "Return an explicit empty state" in agent.prompts[1]
        assert "untrusted review data" in agent.prompts[1]
        assert FAKE_INSTALLATION_TOKEN not in agent.prompts[1]
        assert "SYNTHETIC-TEST-MATERIAL-MUST-NOT-PERSIST" not in agent.prompts[1]

        second_event_count = len(control_plane.get_events(job_id))
        second_replay = runner.run(
            job_id,
            WORKER_TOKEN,
            REPO,
            "main",
            "implement the feature",
            "delivery-1",
            "pytest -q",
            60,
        )
        assert second_replay.state == "PR_UPDATED"
        assert second_replay.commit_sha == second_sha
        assert len(agent.prompts) == 2
        assert git.commit_calls == 2
        assert len(git.pushes) == 2
        assert github.pr_calls == 1
        assert broker.calls == 5
        assert len(control_plane.get_events(job_id)) == second_event_count

        github.head_sha = second_sha
        final_signal = scheduler.review_phase(job_id, "success", reviewer)
        assert final_signal["action"] == "await_user"
        assert review_calls == 2
        assert control_plane.get_job(job_id)["state"] == "await_user"
        event_types = [
            event["event_type"] for event in control_plane.get_events(job_id)
        ]
        assert event_types.count("pr_created") == 1
        assert event_types.count("round2_push") == 1
        control_plane.conn.close()


def test_pending_review_feedback_uses_durable_ids_not_presentation_order():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        control_plane = ControlPlane(
            str(root / "jobs.sqlite"),
            allowed_token_hashes={hash_token(WORKER_TOKEN)},
        )
        control_plane.register(WORKER_TOKEN, "same-coding-agent")
        job_id = control_plane.create_job(
            {"delivery_id": "mixed-order"},
            task_id="mixed-order",
            repo=REPO,
            role="coding_agent",
        )
        control_plane.claim_job(WORKER_TOKEN, job_id)
        control_plane.post_events(
            WORKER_TOKEN,
            job_id,
            [
                {"type": "progress", "payload": {"step": 1}},
                {"type": "progress", "payload": {"step": 2}},
            ],
        )
        control_plane.append_event(job_id, {
            "type": "pr_created",
            "payload": {"pr_number": 12},
        })
        control_plane.append_event(job_id, {
            "type": "review",
            "payload": {
                "verdict": "REQUEST_CHANGES",
                "summary": "Mixed-order feedback",
                "findings": [
                    {"severity": "blocking", "title": "Fix ordering"}
                ],
            },
            "source_type": "scheduler",
            "source_id": "scheduler",
            "actor_role": "reviewer",
        })
        control_plane.append_event(job_id, {
            "type": "round2_label",
            "payload": {"pr_number": 12},
            "source_type": "scheduler",
            "source_id": "scheduler",
            "actor_role": "scheduler",
        })
        control_plane.update_job(job_id, round=2)
        runner = HostAgentJobRunner(
            control_plane=control_plane,
            token_broker=RecordingBroker(),
            agent_runner=RecordingAgent(),
            repository_preparer=RecordingPreparer(),
            git_operations=RecordingGitOperations(),
            docker_backend_factory=lambda repo_path: PassingDocker(),
            github_client=RecordingGitHub(),
            work_root=root / "work",
        )

        feedback = runner._pending_review_feedback(job_id, 2)

        assert feedback is not None
        assert feedback["summary"] == "Mixed-order feedback"
        control_plane.conn.close()


def test_scheduler_duplicate_review_retry_reuses_pending_round2_signal():
    with tempfile.TemporaryDirectory() as tmp:
        control_plane = ControlPlane(str(Path(tmp) / "jobs.sqlite"))
        job_id = control_plane.create_job(
            {"delivery_id": "scheduler-retry"},
            task_id="scheduler-retry",
            repo=REPO,
            role="coding_agent",
        )
        head_sha = "a" * 40
        control_plane.update_job(
            job_id,
            state="agent_done",
            pr_number=12,
            commit_sha=head_sha,
            round=1,
        )
        github = RecordingGitHub()
        github.head_sha = head_sha
        calls = 0

        def request_changes(_pr_state, _evidence):
            nonlocal calls
            calls += 1
            return ReviewVerdict(
                "REQUEST_CHANGES",
                findings=[ReviewFinding("blocking", "Retry safely")],
            )

        scheduler = Scheduler(control_plane, github, REPO)
        reviewer = Reviewer(request_changes)
        first = scheduler.review_phase(job_id, "success", reviewer)
        second = scheduler.review_phase(job_id, "success", reviewer)

        assert first["action"] == second["action"] == "rework"
        assert first["round"] == second["round"] == 2
        assert second["replayed"] is True
        assert calls == 1
        assert github.labels == ["round-2"]
        event_types = [
            event["event_type"] for event in control_plane.get_events(job_id)
        ]
        assert event_types.count("review") == 1
        assert event_types.count("round2_label") == 1
        assert control_plane.get_job(job_id)["round"] == 2
        control_plane.conn.close()


def test_scheduler_recovers_review_persisted_before_round2_label():
    with tempfile.TemporaryDirectory() as tmp:
        control_plane = ControlPlane(str(Path(tmp) / "jobs.sqlite"))
        job_id = control_plane.create_job(
            {"delivery_id": "partial-signal"},
            task_id="partial-signal",
            repo=REPO,
            role="coding_agent",
        )
        head_sha = "b" * 40
        control_plane.update_job(
            job_id,
            state="agent_done",
            pr_number=12,
            commit_sha=head_sha,
            round=1,
        )
        control_plane.append_event(job_id, {
            "type": "review",
            "payload": {
                "verdict": "REQUEST_CHANGES",
                "summary": "Persisted before crash",
                "findings": [
                    {"severity": "blocking", "title": "Resume signal"}
                ],
            },
            "source_type": "scheduler",
            "source_id": "scheduler",
            "actor_role": "reviewer",
        })
        github = RecordingGitHub()
        github.head_sha = head_sha

        def must_not_review(_pr_state, _evidence):
            raise AssertionError("persisted review must be reused")

        signal = Scheduler(control_plane, github, REPO).review_phase(
            job_id,
            "success",
            Reviewer(must_not_review),
        )

        assert signal["action"] == "rework"
        assert signal["replayed"] is True
        assert github.labels == ["round-2"]
        assert control_plane.get_job(job_id)["round"] == 2
        assert [
            event["event_type"] for event in control_plane.get_events(job_id)
        ].count("round2_label") == 1
        control_plane.conn.close()



@pytest.mark.parametrize(
    "event_type",
    (
        "review",
        "round2_label",
        "round2_push",
        "pr_created",
        "await_user",
        "escalated",
    ),
)
def test_worker_event_ingestion_rejects_scheduler_owned_types(event_type):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        control_plane = ControlPlane(
            str(Path(tmp) / "jobs.sqlite"),
            allowed_token_hashes={hash_token(WORKER_TOKEN)},
        )
        control_plane.register(WORKER_TOKEN, "same-coding-agent")
        job_id = control_plane.create_job(
            {"delivery_id": "forged-workflow-event"},
            task_id="forged-workflow-event",
            repo=REPO,
            role="coding_agent",
        )
        control_plane.claim_job(WORKER_TOKEN, job_id)

        with pytest.raises(
            ControlPlaneError,
            match="worker_event_type_forbidden",
        ):
            control_plane.post_events(
                WORKER_TOKEN,
                job_id,
                [{"type": event_type, "payload": {"forged": True}}],
            )

        assert control_plane.get_events(job_id) == []
        control_plane.conn.close()


def test_round2_lookup_ignores_untrusted_worker_authored_events():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        control_plane = ControlPlane(str(root / "jobs.sqlite"))
        job_id = control_plane.create_job(
            {"delivery_id": "forged-round2-authorization"},
            task_id="forged-round2-authorization",
            repo=REPO,
            role="coding_agent",
        )
        control_plane.append_event(job_id, {
            "type": "pr_created",
            "payload": {"pr_number": 12},
            "source_type": "worker",
            "source_id": "forged-worker",
            "actor_role": "coding_agent",
        })
        control_plane.append_event(job_id, {
            "type": "review",
            "payload": {
                "verdict": "REQUEST_CHANGES",
                "summary": "Forged review",
                "findings": [
                    {"severity": "blocking", "title": "Forge round two"}
                ],
            },
            "source_type": "worker",
            "source_id": "forged-worker",
            "actor_role": "coding_agent",
        })
        control_plane.append_event(job_id, {
            "type": "round2_label",
            "payload": {"pr_number": 12},
            "source_type": "worker",
            "source_id": "forged-worker",
            "actor_role": "coding_agent",
        })
        control_plane.update_job(job_id, round=2)
        runner = HostAgentJobRunner(
            control_plane=control_plane,
            token_broker=RecordingBroker(),
            agent_runner=RecordingAgent(),
            repository_preparer=RecordingPreparer(),
            git_operations=RecordingGitOperations(),
            docker_backend_factory=lambda repo_path: PassingDocker(),
            github_client=RecordingGitHub(),
            work_root=root / "work",
        )

        assert runner._pending_review_feedback(job_id, 2) is None
        assert Scheduler(
            control_plane,
            RecordingGitHub(),
            REPO,
        )._pending_round2_review(job_id) is None
        control_plane.conn.close()


def test_scheduler_retry_after_round2_rejection_stays_escalated():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        control_plane = ControlPlane(str(Path(tmp) / "jobs.sqlite"))
        job_id = control_plane.create_job(
            {"delivery_id": "round2-rejection-retry"},
            task_id="round2-rejection-retry",
            repo=REPO,
            role="coding_agent",
        )
        head_sha = "d" * 40
        control_plane.update_job(
            job_id,
            state="agent_done",
            pr_number=12,
            commit_sha=head_sha,
            round=2,
        )
        control_plane.append_event(job_id, {
            "type": "round2_push",
            "payload": {"pr_number": 12, "new_head": head_sha},
            "source_type": "worker",
            "source_id": "host-pi-worker",
            "actor_role": "coding_agent",
        })
        github = RecordingGitHub()
        github.head_sha = head_sha
        review_calls = 0

        def reject_round2(_pr_state, _evidence):
            nonlocal review_calls
            review_calls += 1
            return ReviewVerdict(
                "REQUEST_CHANGES",
                findings=[ReviewFinding("blocking", "Round two still fails")],
            )

        scheduler = Scheduler(control_plane, github, REPO)
        reviewer = Reviewer(reject_round2)
        first = scheduler.review_phase(job_id, "success", reviewer)
        second = scheduler.review_phase(job_id, "success", reviewer)

        assert first["action"] == "escalated"
        assert second["action"] == "escalated"
        assert second["replayed"] is True
        assert review_calls == 1
        assert control_plane.get_job(job_id)["state"] == "escalated"
        event_types = [
            event["event_type"] for event in control_plane.get_events(job_id)
        ]
        assert event_types.count("review") == 1
        assert event_types.count("escalated") == 1
        control_plane.conn.close()


def test_scheduler_recovers_persisted_round2_rejection_as_escalation():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        control_plane = ControlPlane(str(Path(tmp) / "jobs.sqlite"))
        job_id = control_plane.create_job(
            {"delivery_id": "round2-rejection-crash"},
            task_id="round2-rejection-crash",
            repo=REPO,
            role="coding_agent",
        )
        head_sha = "e" * 40
        control_plane.update_job(
            job_id,
            state="agent_done",
            pr_number=12,
            commit_sha=head_sha,
            round=2,
        )
        control_plane.append_event(job_id, {
            "type": "round2_push",
            "payload": {"pr_number": 12, "new_head": head_sha},
            "source_type": "worker",
            "source_id": "host-pi-worker",
            "actor_role": "coding_agent",
        })
        control_plane.append_event(job_id, {
            "type": "review",
            "payload": {
                "verdict": "REQUEST_CHANGES",
                "findings": [
                    {"severity": "blocking", "title": "Persisted rejection"}
                ],
            },
            "source_type": "scheduler",
            "source_id": "scheduler",
            "actor_role": "reviewer",
        })
        github = RecordingGitHub()
        github.head_sha = head_sha

        def must_not_review(_pr_state, _evidence):
            raise AssertionError("persisted round-2 review must be reused")

        signal = Scheduler(control_plane, github, REPO).review_phase(
            job_id,
            "success",
            Reviewer(must_not_review),
        )

        assert signal["action"] == "escalated"
        assert signal["replayed"] is True
        assert control_plane.get_job(job_id)["state"] == "escalated"
        control_plane.conn.close()


def test_scheduler_rejects_ci_observed_for_a_different_head():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        control_plane = ControlPlane(str(Path(tmp) / "jobs.sqlite"))
        job_id = control_plane.create_job(
            {"delivery_id": "moving-head"},
            task_id="moving-head",
            repo=REPO,
            role="coding_agent",
        )
        expected_head = "f" * 40
        moved_head = "1" * 40
        control_plane.update_job(
            job_id,
            state="agent_done",
            pr_number=12,
            commit_sha=expected_head,
            round=1,
        )

        class MovingHeadGitHub(RecordingGitHub):
            def __init__(self):
                super().__init__()
                self.pr_reads = 0
                self.ci_expected_heads: list[str | None] = []

            def get_pr(self, pr_number):
                self.pr_reads += 1
                head_sha = expected_head if self.pr_reads == 1 else moved_head
                return {
                    "number": pr_number,
                    "head_sha": head_sha,
                    "draft": True,
                }

            def get_ci_status(self, pr_number, expected_head=None):
                self.ci_expected_heads.append(expected_head)
                return "success"

        github = MovingHeadGitHub()

        def must_not_review(_pr_state, _evidence):
            raise AssertionError("review must not run after the PR head moves")

        signal = Scheduler(control_plane, github, REPO).review_phase(
            job_id,
            "success",
            Reviewer(must_not_review),
        )

        assert signal["action"] == "head_mismatch"
        assert github.ci_expected_heads == [expected_head]
        assert github.pr_reads == 2
        assert control_plane.get_job(job_id)["state"] != "await_user"
        control_plane.conn.close()



def test_scheduler_rejects_head_moved_during_reviewer_callback():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        control_plane = ControlPlane(str(Path(tmp) / "jobs.sqlite"))
        job_id = control_plane.create_job(
            {"delivery_id": "review-moving-head"},
            task_id="review-moving-head",
            repo=REPO,
            role="coding_agent",
        )
        expected_head = "2" * 40
        moved_head = "3" * 40
        control_plane.update_job(
            job_id,
            state="agent_done",
            pr_number=12,
            commit_sha=expected_head,
            round=1,
        )
        github = RecordingGitHub()
        github.head_sha = expected_head

        def move_head_then_approve(_pr_state, _evidence):
            github.head_sha = moved_head
            return ReviewVerdict("APPROVE")

        signal = Scheduler(control_plane, github, REPO).review_phase(
            job_id,
            "success",
            Reviewer(move_head_then_approve),
        )

        assert signal["action"] == "head_mismatch"
        assert control_plane.get_job(job_id)["state"] != "await_user"
        assert not any(
            event["event_type"] == "review"
            for event in control_plane.get_events(job_id)
        )
        control_plane.conn.close()


def test_unknown_draft_pr_outcome_blocks_retry_instead_of_creating_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        control_plane = ControlPlane(
            str(root / "jobs.sqlite"),
            lease_seconds=1,
            allowed_token_hashes={hash_token(WORKER_TOKEN)},
        )
        control_plane.register(WORKER_TOKEN, "same-coding-agent")
        job_id = control_plane.create_job(
            {"delivery_id": "delivery-crash"},
            task_id="task-crash",
            repo=REPO,
            role="coding_agent",
        )
        github = CrashAfterDraftPrGitHub()
        runner = HostAgentJobRunner(
            control_plane=control_plane,
            token_broker=RecordingBroker(),
            agent_runner=RecordingAgent(),
            repository_preparer=RecordingPreparer(),
            git_operations=RecordingGitOperations(),
            docker_backend_factory=lambda repo_path: PassingDocker(),
            github_client=github,
            work_root=root / "work",
        )

        try:
            runner.run(
                job_id,
                WORKER_TOKEN,
                REPO,
                "main",
                "implement",
                "delivery-crash",
                "pytest -q",
                60,
            )
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("synthetic process crash was not raised")

        control_plane.update_job(job_id, lease_expires=0)
        assert control_plane.reap_expired_leases(now=1) == 1
        result = runner.run(
            job_id,
            WORKER_TOKEN,
            REPO,
            "main",
            "implement",
            "delivery-crash",
            "pytest -q",
            60,
        )

        assert result.state == "BLOCKED"
        assert result.error == "draft_pr_creation_outcome_unknown"
        assert github.pr_calls == 1
        control_plane.conn.close()


class ResumeBranchCommandRunner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, args, *, cwd, env, timeout):
        args = list(args)
        self.calls.append(args)
        if args[1:] == ["init"]:
            (Path(cwd) / ".git" / "info").mkdir(parents=True, exist_ok=True)
        return CommandResult(0, "", "")


def test_repository_preparer_fetches_existing_task_branch_for_rework():
    with tempfile.TemporaryDirectory() as tmp:
        command_runner = ResumeBranchCommandRunner()
        preparer = RepositoryPreparer(
            git_binary="git-test",
            command_runner=command_runner,
            environ={"PATH": os.environ.get("PATH", "")},
        )
        result = preparer.prepare(
            Path(tmp) / "task-repo",
            REPO,
            "main",
            "pi/job-9-delivery",
            FAKE_INSTALLATION_TOKEN,
            resume_existing_branch=True,
        )

        assert result.ok
        commands = [" ".join(call) for call in command_runner.calls]
        assert any(
            command.endswith(
                "fetch --depth 1 origin pi/job-9-delivery"
            )
            for command in commands
        )
        assert any(
            command.endswith(
                "checkout -B pi/job-9-delivery FETCH_HEAD"
            )
            for command in commands
        )
        assert not any(
            command.endswith("fetch --depth 1 origin main")
            for command in commands
        )


def test_idempotent_replay_rejects_a_different_registered_worker():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        control_plane = ControlPlane(
            str(root / "jobs.sqlite"),
            allowed_token_hashes={
                hash_token(WORKER_TOKEN),
                hash_token(OTHER_WORKER_TOKEN),
            },
        )
        control_plane.register(WORKER_TOKEN, "original-coding-agent")
        control_plane.register(OTHER_WORKER_TOKEN, "different-coding-agent")
        job_id = control_plane.create_job(
            {"delivery_id": "delivery-owned"},
            task_id="task-owned",
            repo=REPO,
            role="coding_agent",
        )
        broker = RecordingBroker()
        agent = RecordingAgent()
        git = RecordingGitOperations()
        github = RecordingGitHub()
        runner = HostAgentJobRunner(
            control_plane=control_plane,
            token_broker=broker,
            agent_runner=agent,
            repository_preparer=RecordingPreparer(),
            git_operations=git,
            docker_backend_factory=lambda repo_path: PassingDocker(),
            github_client=github,
            work_root=root / "work",
        )
        runner.run(
            job_id,
            WORKER_TOKEN,
            REPO,
            "main",
            "implement",
            "delivery-owned",
            "pytest -q",
            60,
        )
        event_count = len(control_plane.get_events(job_id))

        try:
            runner.run(
                job_id,
                OTHER_WORKER_TOKEN,
                REPO,
                "main",
                "implement",
                "delivery-owned",
                "pytest -q",
                60,
            )
        except ControlPlaneError as exc:
            assert str(exc) == "worker_identity_mismatch"
        else:
            raise AssertionError("a different Worker replayed the delivery")

        assert len(agent.prompts) == 1
        assert git.commit_calls == 1
        assert len(git.pushes) == 1
        assert github.pr_calls == 1
        assert len(control_plane.get_events(job_id)) == event_count
        control_plane.conn.close()


def test_app_scope_attestation_accepts_normalized_single_smoke_repo():
    api = FakeAppApiClient(accessible_repositories=[{
        "html_url": (
            "https://github.com/YZHLX/"
            "HERMES-OPEN-SWE-SMOKE-TEST.git"
        )
    }])
    broker = GitHubAppTokenBroker(
        app_id="offline-app",
        installation_id="offline-installation",
        app_api=api,
        jwt_signer=lambda *_args: "offline-jwt",
    )

    assert broker.mint_installation_token(REPO)
    assert len(api.scope_checks) == 1
    assert len(api.exchanges) == 1
    assert api.exchanges[0]["repositories"] == [REPO]


@pytest.mark.parametrize(
    "repositories",
    (
        [],
        ["yzhlx/unapproved-repository"],
        [REPO, "yzhlx/unapproved-repository"],
        ["https://example.invalid/yzhlx/hermes-open-swe-smoke-test.git"],
    ),
)
def test_app_scope_attestation_denies_missing_wrong_extra_or_non_github_scope(
    repositories,
):
    api = FakeAppApiClient(accessible_repositories=repositories)
    broker = GitHubAppTokenBroker(
        app_id="offline-app",
        installation_id="offline-installation",
        app_api=api,
        jwt_signer=lambda *_args: "offline-jwt",
    )

    with pytest.raises(ControlPlaneError, match="installation_scope_denied"):
        broker.mint_installation_token(REPO)

    assert api.exchanges == []


def test_app_scope_attestation_fails_closed_when_query_is_unavailable():
    class ScopeQueryFailure(FakeAppApiClient):
        def list_installation_repositories(self, jwt, installation_id):
            raise RuntimeError("offline query failure")

    api = ScopeQueryFailure()
    broker = GitHubAppTokenBroker(
        app_id="offline-app",
        installation_id="offline-installation",
        app_api=api,
        jwt_signer=lambda *_args: "offline-jwt",
    )

    with pytest.raises(ControlPlaneError, match="installation_scope_unverified"):
        broker.mint_installation_token(REPO)

    assert api.exchanges == []


def test_host_rest_adapter_supports_scheduler_reads_and_label_without_token_storage():
    responses = iter([
        {
            "number": 12,
            "head": {"sha": "c" * 40},
            "draft": True,
            "merged": False,
            "state": "open",
        },
        {
            "number": 12,
            "head": {"sha": "c" * 40},
            "draft": True,
            "merged": False,
            "state": "open",
        },
        {
            "check_runs": [
                {"status": "completed", "conclusion": "success"}
            ]
        },
        {"labels": [{"name": "round-2"}]},
    ])
    requests = []
    provider_calls = 0

    def token_provider():
        nonlocal provider_calls
        provider_calls += 1
        return "offline-ephemeral-token"

    def fake_urlopen(request, timeout):
        assert timeout == 45
        requests.append(request)
        return JsonResponse(next(responses))

    client = GitHubRestClient(
        api_base="https://api.example.invalid",
        repo=REPO,
        token_provider=token_provider,
    )
    with mock.patch(
        "hermes_worker.github_client.urllib.request.urlopen",
        side_effect=fake_urlopen,
    ):
        pr = client.get_pr(12)
        ci_status = client.get_ci_status(12)
        client.add_label(12, "round-2")

    assert pr["head_sha"] == "c" * 40
    assert ci_status == "success"
    assert provider_calls == 3
    assert [request.get_method() for request in requests] == [
        "GET", "GET", "GET", "POST"
    ]
    assert requests[-1].full_url.endswith("/repos/yzhlx/hermes-open-swe-smoke-test/issues/12/labels")
    assert not hasattr(client, "token")


def test_host_worker_loads_existing_app_configuration_without_printing_values():
    with tempfile.TemporaryDirectory() as tmp:
        pem = Path(tmp) / "github-app.pem"
        fake_private_key = "FAKE TEST PRIVATE KEY MATERIAL"
        pem.write_text(fake_private_key, encoding="utf-8")
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        fake_api = object()

        with (
            mock.patch.dict(
                os.environ,
                {
                    "HERMES_GITHUB_APP_ID": "12345",
                    "HERMES_GITHUB_INSTALLATION_ID": "67890",
                    "HERMES_GITHUB_APP_PRIVATE_KEY_PATH": str(pem),
                },
                clear=True,
            ),
            mock.patch(
                "hermes_worker.host_agent_job_runner.RealAppApiClient",
                return_value=fake_api,
            ),
            mock.patch(
                "hermes_worker.host_agent_job_runner.GitHubAppTokenBroker",
                side_effect=lambda **kwargs: kwargs,
            ),
            redirect_stdout(captured_stdout),
            redirect_stderr(captured_stderr),
        ):
            loaded = _load_broker()

        assert loaded["app_id"] == "12345"
        assert loaded["installation_id"] == "67890"
        assert loaded["private_key_pem"] == fake_private_key
        assert loaded["app_api"] is fake_api
        output = captured_stdout.getvalue() + captured_stderr.getvalue()
        assert "12345" not in output
        assert "67890" not in output
        assert fake_private_key not in output
