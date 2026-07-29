"""Regression contracts for Fake GitHub PR-head tracking across round 2."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from hermes_worker.agent_runner import FakeAgentRunner
from hermes_worker.constants import ROLE_CODING_AGENT
from hermes_worker.control_plane import ControlPlane
from hermes_worker.db import hash_token
from hermes_worker.echo_sandbox import EchoSandboxBackend
from hermes_worker.github_app import FakeAppApiClient, GitHubAppTokenBroker
from hermes_worker.github_client import FakeGitHubClient
from hermes_worker.reviewer import ReviewFinding, ReviewVerdict, Reviewer
from hermes_worker.scheduler import D3Orchestrator, Scheduler, WorkerAgent


ALLOWED_REPO = "yzhlx/hermes-open-swe-smoke-test"
WORKER_TOKEN = "wk-fake-pr-head-sync"


def _fake_jwt(app_id, pem, ts):
    return "fake.jwt.token"


class CountingReviewer(Reviewer):
    def __init__(self, approve_after: int = 2):
        super().__init__(decide=self._decide)
        self.approve_after = approve_after
        self.calls = 0

    def _decide(self, pr_state, evidence):
        self.calls += 1
        if self.calls < self.approve_after:
            return ReviewVerdict(
                "REQUEST_CHANGES",
                findings=[ReviewFinding("blocking", "round 2 required")],
            )
        return ReviewVerdict("APPROVE", findings=[])


class NeverCalledReviewer:
    def review(self, pr_state, evidence):
        raise AssertionError("Reviewer must not run after a PR-head mismatch")


@dataclass
class D3Harness:
    cp: ControlPlane
    github: FakeGitHubClient
    broker: GitHubAppTokenBroker
    agent: FakeAgentRunner
    sandbox: EchoSandboxBackend


@pytest.fixture
def d3_harness(tmp_path):
    cp = ControlPlane(
        str(tmp_path / "fake-pr-head.db"),
        allowed_token_hashes={hash_token(WORKER_TOKEN)},
        lease_seconds=1_200,
    )
    cp.register(WORKER_TOKEN)
    harness = D3Harness(
        cp=cp,
        github=FakeGitHubClient(),
        broker=GitHubAppTokenBroker(
            app_id="12345",
            installation_id="67890",
            app_api=FakeAppApiClient(),
            jwt_signer=_fake_jwt,
            allowed_repos={ALLOWED_REPO},
            ttl_seconds=3_600,
        ),
        agent=FakeAgentRunner(sha_fn=lambda round_number: f"sha-round{round_number}"),
        sandbox=EchoSandboxBackend(),
    )
    try:
        yield harness
    finally:
        cp.conn.close()


def test_round2_fake_pr_head_tracks_same_branch_before_acceptance(d3_harness):
    job_id, created = d3_harness.cp.create_issue_task(
        ALLOWED_REPO,
        901,
        {"command": "round-2 PR-head regression"},
        role=ROLE_CODING_AGENT,
    )
    assert created is True
    reviewer = CountingReviewer(approve_after=2)
    orchestrator = D3Orchestrator(
        d3_harness.cp,
        d3_harness.github,
        d3_harness.broker,
        d3_harness.agent,
        reviewer,
        d3_harness.sandbox,
        ALLOWED_REPO,
    )

    signal = orchestrator.run_job(
        job_id,
        WORKER_TOKEN,
        "implement",
        ci_status="success",
    )

    assert signal["action"] == "await_user"
    assert reviewer.calls == 2
    job = d3_harness.cp.get_job(job_id)
    assert job["round"] == 2
    pr_number = job["pr_number"]
    assert d3_harness.github.pr_numbers() == [pr_number]
    assert d3_harness.github.get_pr(pr_number)["head_sha"] == (
        job["commit_sha"]
    ) == "sha-round2"


def test_wrong_job_sha_remains_head_mismatch_and_never_awaits_user(d3_harness):
    job_id, created = d3_harness.cp.create_issue_task(
        ALLOWED_REPO,
        902,
        {"command": "real head-mismatch regression"},
        role=ROLE_CODING_AGENT,
    )
    assert created is True
    worker = WorkerAgent(
        d3_harness.cp,
        d3_harness.github,
        d3_harness.broker,
        d3_harness.agent,
        ALLOWED_REPO,
    )
    worker.run_phase(
        job_id,
        WORKER_TOKEN,
        1,
        "implement",
        d3_harness.sandbox,
    )
    job = d3_harness.cp.get_job(job_id)
    pr_number = job["pr_number"]
    assert d3_harness.github.get_pr(pr_number)["head_sha"] == "sha-round1"
    d3_harness.github.set_ci_status(pr_number, "success")
    d3_harness.cp.update_job(job_id, commit_sha="sha-not-the-pr-head")

    signal = Scheduler(
        d3_harness.cp,
        d3_harness.github,
        ALLOWED_REPO,
    ).review_phase(
        job_id,
        "success",
        NeverCalledReviewer(),
    )

    assert signal["action"] == "head_mismatch"
    assert d3_harness.cp.get_job(job_id)["state"] != "await_user"
